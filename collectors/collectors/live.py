"""실시간 문자중계 수집기(COL-06, 설계서 1.4, 4.3, 4.7 LiveCollector).

동작 흐름
  15초마다: PostgreSQL `match`에서 10분 안에 시작하거나 진행 중인 오늘 경기를 고른다
         → 경기마다 지금 세트의 GET /stat/real-time (Airflow와 함께 1초 1회 예산을 나눠 씀, NFR-01)
         → 아직 보내지 않은 이벤트만 → Kafka kovo.live (키 = 시즌-경기번호, 예: 023-7)

메시지의 payload 형식(flink_jobs/live_metrics.py가 읽음):
  {"match": "023-7", "set": 2, "start": 57, "events": [...KOVO 이벤트 57번, 58번, ...], "mode": "LIVE", "session": "live"}
배치 수집기와 달리 응답 전체가 아니라 '새로 생긴 부분'만 보낸다. KOVO는 매번 세트 전체를 돌려주는데,
경기 중에는 새 이벤트만 의미가 있기 때문이다. 문자중계 전체는 경기 후 kovo_postgame이 kovo.rally로 다시
수집하며, 그쪽이 공식 기록이 된다.

세트 종료 이벤트(ET)가 보이면 그 세트가 끝난 것으로 보고 다음 세트로 넘어간다. 한 팀이 3세트를 이기면 멈춘다.
재시작하면 Flink가 저장해 둔 live_match_state.last_event_seq부터 이어서 받는다. 이때 몇 개가 두 번 갈 수
있는데, Flink가 이벤트 위치(순번)로 중복을 버린다. 수집 로그는 경기마다 하나씩 남긴다(COL-08).

  python -m collectors live [--every 15]            (compose의 live-collector 서비스, stream 프로필)
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
SEQ_BASE = 10000  # 이벤트 순번 = 세트번호 × 10000 + 세트 안 위치. flink_jobs/live_state.py와 같은 값이어야 한다
PAGE_SIZE = 500
MAX_PAGES = 4
SETS_TO_WIN = 3  # 3세트를 먼저 이기면 경기 끝
LEAD = timedelta(minutes=10)  # 예정 시각 10분 전부터 확인을 시작한다
GIVE_UP = timedelta(hours=4)  # 그때까지 경기 종료를 못 보면 포기한다(결과는 kovo_postgame이 수집)

# 지금 따라가야 할 경기: 아직 끝나지 않았고, 10분 안에 시작하거나 시작한 지 4시간이 안 된 경기
GAMES_SQL = """
    SELECT s.code, m.game_no, c.gcode, c.league_code, ht.code, at.code, m.scheduled_at
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    JOIN team ht ON ht.id = m.home_team_id JOIN team at ON at.id = m.away_team_id
    WHERE m.status IN ('SCHEDULED', 'LIVE') AND m.scheduled_at <= %(now)s + %(lead)s
      AND m.scheduled_at >= %(now)s - %(give_up)s
    ORDER BY m.scheduled_at, m.game_no
"""
# 재시작했을 때 이어서 받을 위치: Flink가 기록한 마지막 이벤트 순번과 세트 점수
RESUME_SQL = """
    SELECT st.last_event_seq, st.set_scores FROM live_match_state st JOIN match m ON m.id = st.match_id
    JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE st.mode = 'LIVE' AND s.code = %s AND m.game_no = %s
"""


def pg_connect():
    """서비스 DB(PostgreSQL)에 접속한다. 접속 정보는 환경 변수에서 읽는다."""
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def set_winner(feed, hcode):
    """끝난 세트의 이벤트 목록에서 득점(+1 이벤트)을 세어 'home' / 'away'를 돌려준다. 같으면 None.
    rgrade > 0 이 득점 이벤트이고, rtcode는 득점한 팀 코드다."""
    home = sum(1 for e in feed if int(e.get("rgrade") or 0) > 0 and e.get("rtcode") == hcode)
    away = sum(1 for e in feed if int(e.get("rgrade") or 0) > 0 and e.get("rtcode") != hcode)
    return None if home == away else "home" if home > away else "away"


@dataclass
class LiveGame:
    """따라가고 있는 경기 하나의 상태: 지금 몇 세트인지, 그 세트의 이벤트를 몇 개 보냈는지, 세트 승패."""

    season: str
    gnum: int
    gcode: str
    league: str
    hcode: str
    acode: str
    scheduled_at: datetime
    set_no: int = 1  # 지금 따라가는 세트
    sent: int = 0  # 지금 세트에서 이미 보낸 이벤트 수(= 다음에 보낼 이벤트의 위치)
    winners: dict = field(default_factory=dict)  # 세트 번호 -> 'home' / 'away'
    done: bool = False  # 경기가 끝났거나 포기했다
    sent_total: int = 0  # 이 경기에서 보낸 이벤트 총수(수집 로그용)
    log_record: dict | None = None  # 수집 로그의 시작 레코드(끝날 때 finish에 넘김)

    @property
    def key(self):
        return f"{self.season}-{self.gnum}"

    @property
    def sets(self):
        """지금까지 이긴 세트 수: {"home": n, "away": m}"""
        return {side: sum(1 for w in self.winners.values() if w == side) for side in ("home", "away")}

    def resume(self, last_event_seq, set_scores):
        """재시작 후, Flink가 이미 저장한 데까지 건너뛰고 이어 간다.
        set_scores: 끝난 세트들의 [홈 점수, 원정 점수] 목록. last_event_seq: 마지막으로 저장된 이벤트 순번."""
        for i, (h, a) in enumerate(set_scores or []):
            if h != a:
                self.winners[i + 1] = "home" if h > a else "away"
        if last_event_seq:
            self.set_no, self.sent = divmod(last_event_seq, SEQ_BASE)  # 순번을 (세트, 위치)로 되돌림
            self.sent += 1  # 위치는 0부터 시작하므로, 다음에 보낼 것은 그다음 위치

    def fetch_set(self, client):
        """지금 세트의 이벤트를 모두 받아 온다(페이지가 여러 개면 이어 붙임)."""
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
        """지금 세트를 한 번 요청한다. (세트 번호, 시작 위치, 새 이벤트들, 세트 전체 이벤트)를 돌려준다."""
        feed = self.fetch_set(client)
        return self.set_no, self.sent, feed[self.sent:], feed

    def advance(self, set_no, feed):
        """새 이벤트가 Kafka에 전달된 뒤에 부른다. 보낸 위치를 옮기고, 세트가 끝났으면 다음 세트(또는 경기 종료)로."""
        self.sent = len(feed)
        if any(e.get("raction") == "ET" for e in feed):  # ET = 세트 종료 이벤트
            self.winners[set_no] = set_winner(feed, self.hcode)
            if max(self.sets.values()) >= SETS_TO_WIN:
                self.done = True
            else:
                self.set_no, self.sent = set_no + 1, 0

    def params(self, set_no):
        """봉투에 적을 요청 파라미터(배치 수집기의 문자중계 요청과 같은 모양)."""
        return {"gcode": self.gcode, "season": self.season, "leagueCode": self.league, "gnum": self.gnum,
                "hcode": self.hcode, "acode": self.acode, "rset": set_no, "page": 0, "size": PAGE_SIZE}


class LiveCollector:
    """여러 경기를 동시에 따라가며 새 이벤트를 Kafka로 보내는 상주(常住) 수집기."""

    def __init__(self, client, publisher, connect=pg_connect, clock=lambda: datetime.now(timezone.utc)):
        """connect/clock은 테스트에서 가짜 DB·가짜 시계로 바꿔 끼울 수 있다."""
        self.client = client
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)
        self.connect = connect
        self.clock = clock
        self.games = {}  # 경기 키 -> LiveGame
        self.run_id = uuid.uuid4().hex

    def refresh(self):
        """곧 시작하거나 진행 중인 경기를 목록에 더한다. 각 경기는 Flink가 저장한 위치부터 이어 간다."""
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
        """경기 하나를 끝내고 수집 로그를 마무리한다(SUCCESS: 경기 종료 확인, FAILED: 시간 초과로 포기)."""
        game.done = True
        self.logger.finish(game.log_record, status, game.sent_total, error)
        self.publisher.flush()
        log.info("%s %s: %d events sent, sets %s", game.key, status, game.sent_total, game.sets)

    def round(self):
        """모든 경기를 한 번씩 확인한다. Kafka가 받았다고 확인한 뒤에만 '보냄'으로 표시한다
        (중간에 실패하면 다음 회차에 같은 이벤트를 다시 보낸다 — 빠지는 것보다 중복이 낫고, 중복은 Flink가 거른다)."""
        delivered = []
        for game in [g for g in self.games.values() if not g.done]:
            if self.clock() > game.scheduled_at + GIVE_UP:
                self.finish(game, "FAILED", "no end of game seen; kovo_postgame collects the result")
                continue
            try:
                set_no, start, new, feed = game.poll(self.client)
            except Exception as exc:  # KOVO 쪽 일시 오류: 다음 회차에 다시 시도
                log.warning("%s set %d: %s", game.key, game.set_no, exc)
                continue
            if new:
                payload = {"match": game.key, "set": set_no, "start": start, "events": new,
                           "mode": "LIVE", "session": "live"}
                self.publisher.publish(TOPIC, game.key, make_envelope(
                    source="kovo", endpoint="/stat/real-time", params=game.params(set_no), payload=payload,
                    collector=JOB, run_id=self.run_id))
            delivered.append((game, set_no, feed, len(new)))
        self.publisher.flush()  # 여기서 실패하면 아래가 실행되지 않아 '보냄' 표시가 남지 않는다
        for game, set_no, feed, n in delivered:
            game.sent_total += n
            if n:
                log.info("%s set %d: +%d events", game.key, set_no, n)
            game.advance(set_no, feed)
            if game.done:
                self.finish(game, "SUCCESS")
        # 끝난 경기는 시간이 좀 지나면 목록에서 지운다(딕셔너리가 계속 커지지 않게. DB 상태가 다시 고르지 않게 막아 줌)
        for key in [k for k, g in self.games.items() if g.done and self.clock() > g.scheduled_at + GIVE_UP]:
            del self.games[key]

    def run(self, every=15.0, refresh_every=60.0):
        """끝없이 돈다: refresh_every초마다 경기 목록을 갱신하고, every초마다 round()로 새 이벤트를 보낸다."""
        check = getattr(self.publisher, "check", None)
        while check:  # PC 재시작 후 Kafka가 이 서비스보다 늦게 뜰 수 있다: 죽지 말고 기다린다
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
            except Exception as exc:  # Kafka 재시작 중 등: '보냄' 표시를 안 했으므로 다음 회차가 다시 보낸다
                log.warning("round failed: %s", exc)
            time.sleep(max(1.0, every - (time.monotonic() - started)))  # 처리 시간을 빼고 남은 만큼만 쉰다
