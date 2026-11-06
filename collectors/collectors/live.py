"""Live play-by-play collector (COL-06, design.md 1.4, 4.3 and 4.7 LiveCollector).

  every 15 s: today's games from PostgreSQL `match` that start within 10 minutes or are in progress
           -> GET /stat/real-time for each game's current set (1 req/s budget shared with Airflow, NFR-01)
           -> only the events not sent yet -> Kafka kovo.live (key = season-game, e.g. 023-7)

Message payload (read by flink_jobs/live_metrics.py):
  {"match": "023-7", "set": 2, "start": 57, "events": [...KOVO feed events 57, 58, ...], "mode": "LIVE", "session": "live"}
Unlike the batch collectors this sends a slice of the response, not the whole body: KOVO returns the whole set
every time, and only what is new matters while the game is on. The full play-by-play is still collected after the
game by kovo_postgame (kovo.rally), and that copy becomes the official record.

A set is over when its feed has the end-of-set event (ET); the collector then moves to the next set, and stops
after a team wins its third set. Restarting resumes from live_match_state.last_event_seq (written by Flink); a few
events may be sent twice, and Flink drops repeats by their position. One collection log record per game (COL-08).

  python -m collectors live [--every 15]            (compose service live-collector, stream profile)
"""
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .collection_log import CollectionLogger
from .envelope import make_envelope

log = logging.getLogger(__name__)
TOPIC = "kovo.live"
JOB = "kovo.live"
SEQ_BASE = 10000  # same as flink_jobs/live_state.py
PAGE_SIZE = 500
MAX_PAGES = 4
SETS_TO_WIN = 3
LEAD = timedelta(minutes=10)  # start polling this long before the scheduled time
GIVE_UP = timedelta(hours=4)  # no end of game by then: stop (kovo_postgame still collects the result)

GAMES_SQL = """
    SELECT s.code, m.game_no, c.gcode, c.league_code, ht.code, at.code, m.scheduled_at
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    JOIN team ht ON ht.id = m.home_team_id JOIN team at ON at.id = m.away_team_id
    WHERE m.status IN ('SCHEDULED', 'LIVE') AND m.scheduled_at <= %(now)s + %(lead)s
      AND m.scheduled_at >= %(now)s - %(give_up)s
    ORDER BY m.scheduled_at, m.game_no
"""
RESUME_SQL = """
    SELECT st.last_event_seq, st.set_scores FROM live_match_state st JOIN match m ON m.id = st.match_id
    JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE st.mode = 'LIVE' AND s.code = %s AND m.game_no = %s
"""


def pg_connect():
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def set_winner(feed, hcode):
    """'home' / 'away' from the points (+1 events) in a finished set's feed, None for a tie."""
    home = sum(1 for e in feed if int(e.get("rgrade") or 0) > 0 and e.get("rtcode") == hcode)
    away = sum(1 for e in feed if int(e.get("rgrade") or 0) > 0 and e.get("rtcode") != hcode)
    return None if home == away else "home" if home > away else "away"


@dataclass
class LiveGame:
    season: str
    gnum: int
    gcode: str
    league: str
    hcode: str
    acode: str
    scheduled_at: datetime
    set_no: int = 1
    sent: int = 0  # events of the current set already published
    winners: dict = field(default_factory=dict)  # set number -> 'home' / 'away'
    done: bool = False
    sent_total: int = 0
    log_record: dict | None = None

    @property
    def key(self):
        return f"{self.season}-{self.gnum}"

    @property
    def sets(self):
        return {side: sum(1 for w in self.winners.values() if w == side) for side in ("home", "away")}

    def resume(self, last_event_seq, set_scores):
        """Continue after a restart from what Flink has already stored."""
        for i, (h, a) in enumerate(set_scores or []):
            if h != a:
                self.winners[i + 1] = "home" if h > a else "away"
        if last_event_seq:
            self.set_no, self.sent = divmod(last_event_seq, SEQ_BASE)
            self.sent += 1  # positions start at 0

    def fetch_set(self, client):
        feed = []
        for page in range(MAX_PAGES):
            body = client.real_time(self.gcode, self.season, self.gnum, self.hcode, self.acode, self.set_no,
                                    page=page, size=PAGE_SIZE)
            part = (body.get("payload") or {}).get("feed") or []
            feed += part
            if len(part) < PAGE_SIZE:
                break
        return feed

    def poll(self, client):
        """One request round for the current set. Returns (set_no, start, new events, feed)."""
        feed = self.fetch_set(client)
        return self.set_no, self.sent, feed[self.sent:], feed

    def advance(self, set_no, feed):
        """Called after the new events were delivered."""
        self.sent = len(feed)
        if any(e.get("raction") == "ET" for e in feed):
            self.winners[set_no] = set_winner(feed, self.hcode)
            if max(self.sets.values()) >= SETS_TO_WIN:
                self.done = True
            else:
                self.set_no, self.sent = set_no + 1, 0

    def params(self, set_no):
        return {"gcode": self.gcode, "season": self.season, "leagueCode": self.league, "gnum": self.gnum,
                "hcode": self.hcode, "acode": self.acode, "rset": set_no, "page": 0, "size": PAGE_SIZE}


class LiveCollector:
    def __init__(self, client, publisher, connect=pg_connect, clock=lambda: datetime.now(timezone.utc)):
        self.client = client
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)
        self.connect = connect
        self.clock = clock
        self.games = {}  # key -> LiveGame
        self.run_id = uuid.uuid4().hex

    def refresh(self):
        """Add the games that are about to start or in progress; resume each from Flink's stored position."""
        conn = self.connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(GAMES_SQL, {"now": self.clock(), "lead": LEAD, "give_up": GIVE_UP})
                for season, gnum, gcode, league, hcode, acode, at in cur.fetchall():
                    key = f"{season}-{gnum}"
                    if key in self.games:
                        continue
                    game = LiveGame(season, int(gnum), gcode, league, hcode, acode, at)
                    cur.execute(RESUME_SQL, (season, int(gnum)))
                    row = cur.fetchone()
                    if row:
                        game.resume(row[0], row[1])
                    game.log_record = self.logger.start(JOB, f"{key} set {game.set_no}", self.run_id)
                    self.games[key] = game
                    log.info("following %s (%s vs %s, %s) from set %d event %d",
                             key, hcode, acode, at.isoformat(), game.set_no, game.sent)
        finally:
            conn.close()
        self.publisher.flush()

    def finish(self, game, status, error=None):
        game.done = True
        self.logger.finish(game.log_record, status, game.sent_total, error)
        self.publisher.flush()
        log.info("%s %s: %d events sent, sets %s", game.key, status, game.sent_total, game.sets)

    def round(self):
        """Poll every game once. Events are marked sent only after Kafka acknowledged them."""
        delivered = []
        for game in [g for g in self.games.values() if not g.done]:
            if self.clock() > game.scheduled_at + GIVE_UP:
                self.finish(game, "FAILED", "no end of game seen; kovo_postgame collects the result")
                continue
            try:
                set_no, start, new, feed = game.poll(self.client)
            except Exception as exc:  # KOVO hiccup: try again next round
                log.warning("%s set %d: %s", game.key, game.set_no, exc)
                continue
            if new:
                payload = {"match": game.key, "set": set_no, "start": start, "events": new,
                           "mode": "LIVE", "session": "live"}
                self.publisher.publish(TOPIC, game.key, make_envelope(
                    source="kovo", endpoint="/stat/real-time", params=game.params(set_no), payload=payload,
                    collector=JOB, run_id=self.run_id))
            delivered.append((game, set_no, feed, len(new)))
        self.publisher.flush()
        for game, set_no, feed, n in delivered:
            game.sent_total += n
            if n:
                log.info("%s set %d: +%d events", game.key, set_no, n)
            game.advance(set_no, feed)
            if game.done:
                self.finish(game, "SUCCESS")
        # forget finished games after a while so the dict does not grow (the DB status keeps them out)
        for key in [k for k, g in self.games.items() if g.done and self.clock() > g.scheduled_at + GIVE_UP]:
            del self.games[key]

    def run(self, every=15.0, refresh_every=60.0):
        check = getattr(self.publisher, "check", None)
        while check:  # after a PC restart Kafka may come up later than this service: wait, do not crash
            try:
                check()
                break
            except Exception as exc:
                log.warning("waiting for Kafka: %s", exc)
                time.sleep(10)
        log.info("watching for games that start within %d minutes (poll every %.0f s)", LEAD.seconds // 60, every)
        last_refresh = 0.0
        while True:
            started = time.monotonic()
            if started - last_refresh >= refresh_every:
                try:
                    self.refresh()
                    last_refresh = started
                except Exception as exc:
                    log.warning("could not read today's games: %s", exc)
            try:
                self.round()
            except Exception as exc:  # Kafka restarting: nothing was marked sent, so the next round resends
                log.warning("round failed: %s", exc)
            time.sleep(max(1.0, every - (time.monotonic() - started)))
