"""Match replayer (PIPE-11, design.md 1.4 and 4.7 MatchReplayer).

Re-publishes a finished match's play-by-play to kovo.live, one message per rally, at the real pace or faster,
so the live path (Flink live-metrics -> live.metrics -> SSE -> browser) can be developed and shown when no game
is on. Messages say mode=REPLAY and carry a new session id: Flink starts that match over, and the site labels it
as a replay. Replays never change the match's status or official records.

Source: the curated play-by-play in PostgreSQL rally_event (built by Spark from HDFS /raw/topic=kovo.rally). Finding
one game in the raw files would mean reading every collection day, while rally_event has it indexed by match.
Events are rebuilt in the KOVO feed shape, so Flink cannot tell a replay from a live game except by `mode`.
Pace: KOVO gives no time per rally, so each set's official duration (set_score.duration_min) is spread evenly
over its rallies, divided by --speed.

  python -m collectors replay 023-5 [--speed 10] [--from-set 1]      (airflow container, or DAG live_replay)
"""
import logging
import time
import uuid

from .collection_log import CollectionLogger
from .envelope import make_envelope
from .live import pg_connect

log = logging.getLogger(__name__)
TOPIC = "kovo.live"
JOB = "kovo.replay"
DEFAULT_SET_MIN = 25
SET_BREAK_SEC = 180

MATCH_SQL = """
    SELECT m.id, c.gcode, c.league_code, ht.code, at.code, m.status
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    JOIN team ht ON ht.id = m.home_team_id JOIN team at ON at.id = m.away_team_id
    WHERE s.code = %s AND m.game_no = %s
"""
EVENTS_SQL = """
    SELECT r.set_no, r.event_no, r.rally_no, r.seq, t.code, p.code, p.name, r.back_number, r.action_code,
           r.result_code, r.grade
    FROM rally_event r LEFT JOIN team t ON t.id = r.team_id LEFT JOIN player p ON p.id = r.player_id
    WHERE r.match_id = %s AND r.set_no >= %s ORDER BY r.set_no, r.event_no
"""
DURATION_SQL = "SELECT set_no, duration_min FROM set_score WHERE match_id = %s"


def feed_event(row):
    """A rally_event row back in the KOVO feed shape (the fields Flink reads)."""
    _, _, rally_no, seq, team, player, name, back_number, action, result, grade = row
    return {"rtcode": team or "", "rpcode": player or "0000000", "rpname": name or "", "rbnum": back_number or 0,
            "raction": action, "raresult": result, "rgrade": grade, "rindex": seq, "rpage": rally_no}


def rally_messages(rows):
    """[(set_no, start, events)] with one entry per rally, in order. `start` is the position in the set's feed."""
    out = []
    for row in rows:
        set_no, event_no, rally_no = row[0], row[1], row[2]
        if out and out[-1][0] == set_no and out[-1][3] == rally_no:
            out[-1][2].append(feed_event(row))
        else:
            out.append([set_no, event_no - 1, [feed_event(row)], rally_no])
    return [(s, start, events) for s, start, events, _ in out]


class MatchReplayer:
    def __init__(self, publisher, connect=pg_connect, sleep=time.sleep):
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)
        self.connect = connect
        self.sleep = sleep
        self.session = None  # session id of the latest replay (Flink and the site show it)

    def load(self, key, from_set=1):
        season, gnum = key.split("-")
        conn = self.connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(MATCH_SQL, (season, int(gnum)))
                match = cur.fetchone()
                if match is None:
                    raise ValueError(f"match {key} is not in the database")
                cur.execute(EVENTS_SQL, (match[0], from_set))
                rows = cur.fetchall()
                cur.execute(DURATION_SQL, (match[0],))
                durations = dict(cur.fetchall())
        finally:
            conn.close()
        if not rows:
            raise ValueError(f"match {key} has no play-by-play (only finished games from 2023-24 on have it)")
        return match, rows, durations

    def replay(self, key, speed=10.0, from_set=1):
        (_, gcode, league, hcode, acode, _), rows, durations = self.load(key, from_set)
        messages = rally_messages(rows)
        rallies = {}
        for s, _, _ in messages:
            rallies[s] = rallies.get(s, 0) + 1
        session = self.session = uuid.uuid4().hex
        record = self.logger.start(JOB, f"{key} speed {speed}x from set {from_set}", session)
        sent = 0
        try:
            for i, (set_no, start, events) in enumerate(messages):
                if i and messages[i - 1][0] != set_no:
                    self.sleep(max(1.0, SET_BREAK_SEC / speed))
                params = {"gcode": gcode, "season": key.split("-")[0], "leagueCode": league, "gnum": int(key.split("-")[1]),
                          "hcode": hcode, "acode": acode, "rset": set_no}
                payload = {"match": key, "set": set_no, "start": start, "events": events, "mode": "REPLAY",
                           "session": session, "speed": speed}
                self.publisher.publish(TOPIC, key, make_envelope(source="replay", endpoint="/stat/real-time",
                                                                 params=params, payload=payload, collector=JOB,
                                                                 run_id=session))
                self.publisher.flush()
                sent += 1
                if sent % 20 == 0:
                    log.info("%s: set %d, %d/%d rallies sent", key, set_no, sent, len(messages))
                minutes = durations.get(set_no) or DEFAULT_SET_MIN
                self.sleep(minutes * 60 / max(1, rallies[set_no]) / speed)
        except BaseException as exc:  # including Ctrl+C: the log shows how far it got
            self.logger.finish(record, "FAILED", sent, f"{exc.__class__.__name__}: {exc}")
            self.publisher.flush()
            raise
        self.logger.finish(record, "SUCCESS", sent)
        self.publisher.flush()
        return sent
