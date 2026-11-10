"""경기 재생기(PIPE-11, 설계서 1.4, 4.7 MatchReplayer).

끝난 경기의 문자중계를 랠리 하나당 메시지 하나씩, 실제 속도 또는 더 빠르게 kovo.live로 다시 보낸다.
경기가 없는 날에도 실시간 경로(Flink live-metrics → live.metrics → SSE → 브라우저)를 개발하고 시연하기 위해서다.
메시지에는 mode=REPLAY와 새 세션 ID가 붙는다. Flink는 그 경기를 처음부터 다시 계산하고, 사이트는 '다시 보기'로
표시한다. 재생은 경기 상태나 공식 기록을 절대 바꾸지 않는다.

데이터 출처: PostgreSQL rally_event(Spark가 HDFS /raw/topic=kovo.rally에서 정제한 문자중계).
원본 파일에서 경기 하나를 찾으려면 모든 수집 날짜를 읽어야 하지만, rally_event는 경기별로 색인돼 있다.
이벤트는 KOVO 응답과 같은 모양으로 다시 만들어 보내므로, Flink는 `mode` 말고는 실제 경기와 구별하지 못한다.
속도: KOVO는 랠리별 시각을 주지 않는다. 그래서 세트의 공식 경기 시간(set_score.duration_min)을 그 세트의
랠리 수로 똑같이 나누고, --speed 배수로 다시 나눈다.

  python -m collectors replay 023-5 [--speed 10] [--from-set 1]      (airflow 컨테이너, 또는 DAG live_replay)
"""
import logging
import time
import uuid

from .collection_log import CollectionLogger
from .envelope import make_envelope
from .live import pg_connect

log = logging.getLogger(__name__)
TOPIC = "kovo.live"  # 실제 실시간 수집과 같은 토픽(그래서 같은 경로로 처리된다)
JOB = "kovo.replay"
DEFAULT_SET_MIN = 25  # 세트 시간 기록이 없을 때 쓰는 값(분)
SET_BREAK_SEC = 180  # 세트 사이 쉬는 시간(초, 실제 속도 기준)

# 경기 정보: DB id, 대회 코드, 리그 코드, 홈/원정 팀 코드, 상태
MATCH_SQL = """
    SELECT m.id, c.gcode, c.league_code, ht.code, at.code, m.status
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    JOIN team ht ON ht.id = m.home_team_id JOIN team at ON at.id = m.away_team_id
    WHERE s.code = %s AND m.game_no = %s
"""
# 문자중계 이벤트: from_set 세트부터, 세트·이벤트 순서대로
EVENTS_SQL = """
    SELECT r.set_no, r.event_no, r.rally_no, r.seq, t.code, p.code, p.name, r.back_number, r.action_code,
           r.result_code, r.grade
    FROM rally_event r LEFT JOIN team t ON t.id = r.team_id LEFT JOIN player p ON p.id = r.player_id
    WHERE r.match_id = %s AND r.set_no >= %s ORDER BY r.set_no, r.event_no
"""
DURATION_SQL = "SELECT set_no, duration_min FROM set_score WHERE match_id = %s"


def feed_event(row):
    """rally_event 한 행을 KOVO 응답의 이벤트 모양(Flink가 읽는 필드)으로 되돌린다."""
    _, _, rally_no, seq, team, player, name, back_number, action, result, grade = row
    return {"rtcode": team or "", "rpcode": player or "0000000", "rpname": name or "", "rbnum": back_number or 0,
            "raction": action, "raresult": result, "rgrade": grade, "rindex": seq, "rpage": rally_no}


def rally_messages(rows):
    """이벤트 행들을 랠리 단위로 묶어 [(세트, 시작 위치, 이벤트 목록)]을 순서대로 돌려준다.
    시작 위치는 그 세트 이벤트 목록에서의 위치(0부터)로, 실시간 메시지의 `start`와 같은 뜻이다."""
    out = []  # 원소: [세트, 시작 위치, 이벤트들, 랠리 번호]
    for row in rows:
        set_no, event_no, rally_no = row[0], row[1], row[2]
        if out and out[-1][0] == set_no and out[-1][3] == rally_no:
            out[-1][2].append(feed_event(row))  # 같은 랠리면 이어 붙임
        else:
            out.append([set_no, event_no - 1, [feed_event(row)], rally_no])  # event_no는 1부터라 1을 뺀다
    return [(s, start, events) for s, start, events, _ in out]


class MatchReplayer:
    """DB의 문자중계를 읽어 kovo.live로 다시 보낸다."""

    def __init__(self, publisher, connect=pg_connect, sleep=time.sleep):
        """connect/sleep은 테스트에서 가짜 DB·기다리지 않는 sleep으로 바꿔 끼울 수 있다."""
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)
        self.connect = connect
        self.sleep = sleep
        self.session = None  # 최근 재생의 세션 ID(Flink와 사이트가 보여 준다)

    def load(self, key, from_set=1):
        """경기 키("023-5")로 경기 정보, 이벤트 행, 세트별 시간(분)을 읽는다. 문자중계가 없으면 ValueError."""
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
        """경기를 재생하고 보낸 메시지(랠리) 수를 돌려준다. speed=10이면 10배 빠르게."""
        (_, gcode, league, hcode, acode, _), rows, durations = self.load(key, from_set)
        messages = rally_messages(rows)
        rallies = {}  # 세트 -> 랠리 수(랠리 간격 계산용)
        for s, _, _ in messages:
            rallies[s] = rallies.get(s, 0) + 1
        session = self.session = uuid.uuid4().hex
        record = self.logger.start(JOB, f"{key} speed {speed}x from set {from_set}", session)
        sent = 0
        try:
            for i, (set_no, start, events) in enumerate(messages):
                if i and messages[i - 1][0] != set_no:  # 세트가 바뀌면 세트 사이 휴식
                    self.sleep(max(1.0, SET_BREAK_SEC / speed))
                params = {"gcode": gcode, "season": key.split("-")[0], "leagueCode": league, "gnum": int(key.split("-")[1]),
                          "hcode": hcode, "acode": acode, "rset": set_no}
                payload = {"match": key, "set": set_no, "start": start, "events": events, "mode": "REPLAY",
                           "session": session, "speed": speed}
                self.publisher.publish(TOPIC, key, make_envelope(source="replay", endpoint="/stat/real-time",
                                                                 params=params, payload=payload, collector=JOB,
                                                                 run_id=session))
                self.publisher.flush()  # 랠리마다 바로 전달(화면에 한 랠리씩 나타나도록)
                sent += 1
                if sent % 20 == 0:
                    log.info("%s: set %d, %d/%d rallies sent", key, set_no, sent, len(messages))
                minutes = durations.get(set_no) or DEFAULT_SET_MIN
                self.sleep(minutes * 60 / max(1, rallies[set_no]) / speed)  # 세트 시간 ÷ 랠리 수 ÷ 배속
        except BaseException as exc:  # Ctrl+C 포함: 어디까지 보냈는지 로그에 남긴다
            self.logger.finish(record, "FAILED", sent, f"{exc.__class__.__name__}: {exc}")
            self.publisher.flush()
            raise
        self.logger.finish(record, "SUCCESS", sent)
        self.publisher.flush()
        return sent
