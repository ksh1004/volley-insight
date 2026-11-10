"""live-metrics: 문자중계 이벤트로 실시간 경기 상태와 세트별 지표를 계산한다(PIPE-06, ANL-07, 설계서 3.3).

  Kafka kovo.live → 경기별로 묶기(key by) → 점수, 세트, 서브권, 연속 득점, 세트 지표, 승리 확률(live_state.py)
                  → PostgreSQL live_match_state / live_set_metric / live_rally (upsert)
                  → Kafka live.metrics (키 = 경기, 예: 023-7) → SSE 게이트웨이(django-sse) → 브라우저

입력(collectors/collectors/live.py, replay.py가 보냄): 확인 한 번 또는 재생 랠리 하나당 메시지 하나,
  payload = {match, set, start, events: [KOVO 원본 이벤트들], mode: LIVE|REPLAY, session}
  `start`는 첫 이벤트가 그 세트 목록에서 몇 번째인지라서, 이미 본 이벤트를 알아보고 버릴 수 있다.
상태: 경기마다 JSON 딕셔너리 하나(keyed ValueState, 12시간 TTL). `session`이 다른 메시지(같은 경기의 새 재생)가
오면 처음부터 다시 계산하고 그 경기의 행들을 지운다. 실제 경기만 match.status를 SCHEDULED → LIVE → FINISHED로
옮긴다(설계서 4.5). 재생은 match 테이블을 절대 건드리지 않는다.
재시작: Kafka 위치와 상태는 체크포인트에 있다(submit.sh가 가장 최근 것에서 복원). 저장이 upsert라서 재시작 뒤
다시 처리되는 메시지도 문제없다. 처음 시작할 때는 새 메시지부터만 읽는다(latest).

실행(stream 프로필. flink-job-keeper가 계속 제출 상태를 유지한다):
  docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh live_metrics.py live-metrics
"""
import json
import os
from datetime import datetime, timezone

from pyflink.common import Configuration, Row, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.time import Time
from pyflink.datastream import KeyedProcessFunction, MapFunction, RuntimeContext, StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaOffsetResetStrategy, KafkaOffsetsInitializer, KafkaSource
from pyflink.datastream.state import StateTtlConfig, ValueStateDescriptor
from pyflink.table import StreamTableEnvironment

JOBS_DIR = os.environ.get("JOBS_DIR", "/opt/flink/usrlib/jobs")
KAFKA = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
STATE_HOURS = 12  # 경기 상태를 기억하는 시간(마지막 갱신 후)
# 팀의 시즌 기록이 아직 없을 때 쓰는 리그 평균(2025-26 정규리그). 성별 → (사이드아웃률, 브레이크율)
DEFAULT_RATES = {"M": (0.692, 0.308), "F": (0.609, 0.390)}
PRIOR_MATCHES = 5  # 이번 시즌 기록은 대략 이만큼 경기를 치른 뒤부터 온전히 믿는다(그 전에는 지난 시즌 쪽으로 당김)


def now_iso():
    """지금 시각(UTC, 밀리초까지). 메시지에 처리 시각을 남겨 지연을 잴 때 쓴다."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def pg_connect():
    """서비스 DB(PostgreSQL) 접속."""
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def parse(line: str):
    """수집기 봉투를 처리하기 쉬운 납작한 JSON으로 바꾼다. 형식이 잘못됐거나 mode가 LIVE/REPLAY가 아니면 None.
    홈/원정 팀 코드는 봉투의 요청 파라미터(hcode/acode)에서 꺼낸다."""
    try:
        env = json.loads(line)
        p = env["payload"]
        season, gnum = p["match"].split("-")
        batch = {"match": p["match"], "season": season, "gnum": int(gnum), "set": int(p["set"]), "start": int(p["start"]),
                 "events": list(p["events"]), "mode": p.get("mode") or "LIVE", "session": str(p.get("session") or "live"),
                 "home": str(env["params"]["hcode"]), "away": str(env["params"]["acode"]),
                 "fetched_at": env.get("fetched_at")}
        return json.dumps(batch, ensure_ascii=False) if batch["mode"] in ("LIVE", "REPLAY") else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


# 경기의 성별(리그 평균 기본값을 고르기 위해)
MATCH_SQL = """
    SELECT m.gender FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE s.code = %s AND m.game_no = %s
"""
# 두 팀의 시즌별 사이드아웃률·브레이크율(정규리그, 이번 시즌 이하, 최신 시즌부터). DB는 %라서 100으로 나눈다
RATES_SQL = """
    SELECT t.code, s.code, tss.matches, tss.sideout_rate::float8 / 100, tss.break_rate::float8 / 100
    FROM team_season_summary tss JOIN team t ON t.id = tss.team_id
    JOIN competition c ON c.id = tss.competition_id JOIN season s ON s.id = c.season_id
    WHERE t.code = ANY(%s) AND c.gcode = '001' AND c.league_code = '201' AND s.code <= %s
      AND tss.sideout_rate IS NOT NULL AND tss.break_rate IS NOT NULL
    ORDER BY s.code DESC
"""


def team_rates(rows, team, season, default):
    """한 팀의 (사이드아웃률, 브레이크율). 이번 시즌 기록을 쓰되, 경기 수가 적으면 지난 시즌(없으면 리그 평균)
    쪽으로 당긴다: 가중치 w = 경기수 / (경기수 + PRIOR_MATCHES). 예) 5경기면 이번 시즌 50% + 이전 50%."""
    cur = next(((n, so, br) for code, s, n, so, br in rows if code == team and s == season), None)
    prev = next(((so, br) for code, s, n, so, br in rows if code == team and s < season), None) or default
    if cur is None:
        return prev
    n, so, br = cur
    w = n / (n + PRIOR_MATCHES)
    return w * so + (1 - w) * prev[0], w * br + (1 - w) * prev[1]


def match_rates(conn, batch):
    """승리 확률 계산에 쓸 두 팀의 기록(live_state.rally_probs가 읽는 모양). DB에 없는 경기면 None."""
    with conn.cursor() as cur:
        cur.execute(MATCH_SQL, (batch["season"], batch["gnum"]))
        row = cur.fetchone()
        if row is None:
            return None
        default = DEFAULT_RATES.get(row[0], DEFAULT_RATES["M"])
        cur.execute(RATES_SQL, ([batch["home"], batch["away"]], batch["season"]))
        rows = cur.fetchall()
    h = team_rates(rows, batch["home"], batch["season"], default)
    a = team_rates(rows, batch["away"], batch["season"], default)
    return {"home_sideout": round(h[0], 4), "home_break": round(h[1], 4),
            "away_sideout": round(a[0], 4), "away_break": round(a[1], 4)}


class Follow(KeyedProcessFunction):
    """경기마다 상태를 들고 있다가, 이벤트 묶음이 올 때마다 반영하고 바뀐 내용을 내보낸다."""

    def open(self, ctx: RuntimeContext):
        desc = ValueStateDescriptor("match", Types.STRING())
        desc.enable_time_to_live(StateTtlConfig.new_builder(Time.hours(STATE_HOURS))
                                 .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
                                 .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                                 .build())
        self.state = ctx.get_state(desc)
        self.conn = None

    def _rates(self, batch):
        """두 팀 기록을 DB에서 읽는다. 실패해도 경기 점수 중계는 계속돼야 하므로 None(확률만 없음)을 돌려준다."""
        try:
            if self.conn is None or self.conn.closed:
                self.conn = pg_connect()
            with self.conn:
                return match_rates(self.conn, batch)
        except Exception as exc:  # 점수 중계가 멈추는 것보다 확률이 빠지는 편이 낫다
            print(f"live-metrics: rates unavailable for {batch['match']}: {exc}")
            if self.conn is not None:
                self.conn.close()
            return None

    def process_element(self, value, ctx):
        """묶음 하나 처리: 상태 읽기 → (새 세션이면 초기화) → 이벤트 반영 → 바뀐 게 있으면 상태 저장·update 메시지 출력."""
        from live_state import apply_batch, new_state, summary  # TaskManager에서 import(add_python_file로 배포)

        batch = json.loads(value)
        raw = self.state.value()
        st = json.loads(raw) if raw else None
        reset = st is None or st["session"] != batch["session"]  # 처음 보는 경기 또는 새 재생 세션
        if reset:
            st = new_state(batch["match"], batch["home"], batch["away"], batch["mode"], batch["session"],
                           self._rates(batch))
        before = st["last_seq"]
        points = apply_batch(st, batch["set"], batch["start"], batch["events"])
        if not reset and st["last_seq"] == before:
            return  # 새로운 것이 없다(전부 이미 반영한 이벤트)
        self.state.update(json.dumps(st, ensure_ascii=False))
        yield json.dumps({"type": "update", "reset": reset, "state": summary(st), "points": points,
                          "fetched_at": batch["fetched_at"], "processed_at": now_iso()}, ensure_ascii=False)

    def close(self):
        if self.conn is not None:
            self.conn.close()


# 경기 상태 한 행(경기당 1행). reset이면 시작 시각을 새로 찍고, 끝나면 종료 시각을 한 번만 기록한다
STATE_UPSERT = """
    INSERT INTO live_match_state (match_id, mode, session, status, current_set, home_points, away_points, home_sets,
        away_sets, set_scores, serving_team_id, home_run, away_run, home_set_win_prob, home_win_prob, last_play,
        last_event_seq, fetched_at, started_at, finished_at)
    VALUES (%(match_id)s, %(mode)s, %(session)s, %(status)s, %(current_set)s, %(home_points)s, %(away_points)s,
        %(home_sets)s, %(away_sets)s, %(set_scores)s, %(serving_id)s, %(home_run)s, %(away_run)s,
        %(home_set_win_prob)s, %(home_win_prob)s, %(last_play)s, %(last_event_seq)s, %(fetched_at)s, now(),
        CASE WHEN %(status)s = 'FINISHED' THEN now() END)
    ON CONFLICT (match_id) DO UPDATE SET mode = EXCLUDED.mode, session = EXCLUDED.session, status = EXCLUDED.status,
        current_set = EXCLUDED.current_set, home_points = EXCLUDED.home_points, away_points = EXCLUDED.away_points,
        home_sets = EXCLUDED.home_sets, away_sets = EXCLUDED.away_sets, set_scores = EXCLUDED.set_scores,
        serving_team_id = EXCLUDED.serving_team_id, home_run = EXCLUDED.home_run, away_run = EXCLUDED.away_run,
        home_set_win_prob = EXCLUDED.home_set_win_prob, home_win_prob = EXCLUDED.home_win_prob,
        last_play = EXCLUDED.last_play, last_event_seq = EXCLUDED.last_event_seq, fetched_at = EXCLUDED.fetched_at,
        started_at = CASE WHEN %(reset)s THEN now() ELSE live_match_state.started_at END,
        finished_at = CASE WHEN EXCLUDED.status = 'FINISHED'
                           THEN coalesce(live_match_state.finished_at, now()) END,
        updated_at = now()
"""
# 세트·팀별 지표 한 행(공격 시도·성공, 서브 에이스·범실, 블로킹, 범실)
METRIC_UPSERT = """
    INSERT INTO live_set_metric (match_id, set_no, team_id, points, attack_attempts, attack_success,
        attack_success_rate, serve_aces, serve_errors, block_points, errors)
    VALUES (%(match_id)s, %(set)s, %(team_id)s, %(points)s, %(attack_attempts)s, %(attack_success)s,
        %(attack_success_rate)s, %(serve_aces)s, %(serve_errors)s, %(block_points)s, %(errors)s)
    ON CONFLICT (match_id, set_no, team_id) DO UPDATE SET points = EXCLUDED.points,
        attack_attempts = EXCLUDED.attack_attempts, attack_success = EXCLUDED.attack_success,
        attack_success_rate = EXCLUDED.attack_success_rate, serve_aces = EXCLUDED.serve_aces,
        serve_errors = EXCLUDED.serve_errors, block_points = EXCLUDED.block_points, errors = EXCLUDED.errors,
        updated_at = now()
"""
# 득점 하나 = 한 행(점수 흐름 차트와 득점 피드). 같은 득점이 원인 선수가 채워지며 다시 오면 갱신된다
RALLY_UPSERT = """
    INSERT INTO live_rally (match_id, set_no, point_no, rally_no, seq, home_score, away_score, winner_team_id,
        server_team_id, kind, player_id, player_name, action_code, result_code)
    VALUES (%(match_id)s, %(set)s, %(point_no)s, %(rally_no)s, %(seq)s, %(home_score)s, %(away_score)s,
        %(winner_id)s, %(server_id)s, %(kind)s, (SELECT id FROM player WHERE code = %(player_code)s),
        %(player_name)s, %(action)s, %(result)s)
    ON CONFLICT (match_id, set_no, point_no) DO UPDATE SET rally_no = EXCLUDED.rally_no, seq = EXCLUDED.seq,
        home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score, winner_team_id = EXCLUDED.winner_team_id,
        server_team_id = EXCLUDED.server_team_id, kind = EXCLUDED.kind, player_id = EXCLUDED.player_id,
        player_name = EXCLUDED.player_name, action_code = EXCLUDED.action_code, result_code = EXCLUDED.result_code,
        updated_at = now()
"""
# 경기 키(시즌, 경기번호) → DB의 match.id
MATCH_ID_SQL = """
    SELECT m.id FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE s.code = %s AND m.game_no = %s
"""


class Store(MapFunction):
    """update 하나를 PostgreSQL에 upsert 하고(한 트랜잭션), Row(경기 키, live.metrics 메시지)를 돌려준다."""

    def open(self, ctx: RuntimeContext):
        # DB id는 거의 바뀌지 않으므로 한 번 찾은 값을 기억해 둔다(경기 키 → match.id, 팀 코드 → team.id)
        self.conn, self.match_ids, self.team_ids = None, {}, {}

    def _pg(self):
        """열린 DB 연결(끊겼으면 다시 연다)."""
        if self.conn is None or self.conn.closed:
            self.conn = pg_connect()
        return self.conn

    def _ids(self, cur, s):
        """경기와 두 팀의 DB id를 찾아 캐시에 채우고 match.id를 돌려준다(DB에 없는 경기면 None)."""
        key = s["match"]
        if key not in self.match_ids:
            season, gnum = key.split("-")
            cur.execute(MATCH_ID_SQL, (season, int(gnum)))
            row = cur.fetchone()
            self.match_ids[key] = row[0] if row else None
        for code in (s["home"], s["away"]):
            if code not in self.team_ids:
                cur.execute("SELECT id FROM team WHERE code = %s", (code,))
                row = cur.fetchone()
                self.team_ids[code] = row[0] if row else None
        return self.match_ids[key]

    def map(self, value):
        """상태·세트 지표·득점을 저장한다. 새 세션(reset)이면 그 경기의 이전 득점·지표 행을 먼저 지운다."""
        from psycopg2.extras import Json  # 파이썬 객체를 jsonb 칸에 넣기 위한 래퍼

        msg = json.loads(value)
        s, reset = msg["state"], msg["reset"]
        conn = self._pg()
        try:
            with conn, conn.cursor() as cur:
                match_id = self._ids(cur, s)
                if match_id is None:
                    return Row(s["match"], None)  # 서비스 DB에 (아직) 없는 경기: 저장할 것도 보여 줄 것도 없음
                tid = self.team_ids.get
                if reset:
                    cur.execute("DELETE FROM live_rally WHERE match_id = %s", (match_id,))
                    cur.execute("DELETE FROM live_set_metric WHERE match_id = %s", (match_id,))
                cur.execute(STATE_UPSERT, {**s, "match_id": match_id, "reset": reset, "serving_id": tid(s["serving"]),
                                           "set_scores": Json(s["set_scores"]), "last_play": Json(s["last_play"]),
                                           "fetched_at": msg["fetched_at"]})
                for m in s["metrics"]:
                    cur.execute(METRIC_UPSERT, {**m, "match_id": match_id, "team_id": tid(m["team"])})
                for p in msg["points"]:
                    cur.execute(RALLY_UPSERT, {**p, "match_id": match_id, "winner_id": tid(p["winner"]),
                                               "server_id": tid(p["server"])})
                if s["mode"] == "LIVE":  # 설계서 4.5: 실제 경기만 실시간 경로가 상태를 LIVE, FINISHED로 옮긴다
                    if s["status"] == "FINISHED":
                        cur.execute("UPDATE match SET status = 'FINISHED', home_sets = %s, away_sets = %s, updated_at = now() "
                                    "WHERE id = %s AND status IN ('SCHEDULED', 'LIVE')",
                                    (s["home_sets"], s["away_sets"], match_id))
                    else:
                        cur.execute("UPDATE match SET status = 'LIVE', home_sets = %s, away_sets = %s, updated_at = now() "
                                    "WHERE id = %s AND status IN ('SCHEDULED', 'LIVE')",
                                    (s["home_sets"], s["away_sets"], match_id))
        except Exception:
            conn.close()  # 다음 요소에서 다시 연결한다(작업이 재시작되며 이 요소를 다시 처리함)
            raise
        msg["stored_at"] = now_iso()
        return Row(s["match"], json.dumps(msg, ensure_ascii=False))

    def close(self):
        if self.conn is not None:
            self.conn.close()


def log_line(row):
    """저장한 update 하나를 로그 한 줄로(TaskManager 로그 → Filebeat → Kibana). 수집·저장 시각으로 지연을 볼 수 있다."""
    msg = json.loads(row[1])
    s = msg["state"]
    prob = s["home_win_prob"]
    return (f"live {s['match']} {s['mode']} set {s['current_set']} {s['home_points']}:{s['away_points']} "
            f"sets {s['home_sets']}:{s['away_sets']} {s['status']} points+{len(msg['points'])} "
            f"home_win={prob if prob is not None else '-'} fetched={msg['fetched_at']} stored={msg['stored_at']}")


def main():
    """작업 구성: Kafka → 파싱 → 유효성 → 경기별 상태 반영 → DB 저장 → (로그 출력, live.metrics 발행)."""
    config = Configuration()
    config.set_string("pipeline.name", "live-metrics")
    config.set_string("execution.checkpointing.interval", "30 s")
    config.set_string("state.checkpoints.dir", "hdfs://namenode:9000/flink/checkpoints/live-metrics")
    config.set_string("execution.checkpointing.externalized-checkpoint-retention", "RETAIN_ON_CANCELLATION")
    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.add_python_file(os.path.join(JOBS_DIR, "live_state.py"))  # TaskManager에도 이 파일을 보낸다
    t_env = StreamTableEnvironment.create(env)  # Kafka에 키를 붙여 쓰기 위해 Table API도 함께 쓴다

    source = (KafkaSource.builder()
              .set_bootstrap_servers(KAFKA)
              .set_topics("kovo.live")
              .set_group_id("live-metrics")
              # 요구사항 2.5: 실시간 처리는 가장 새 데이터부터 시작한다(지난 메시지를 몰아서 처리하지 않음)
              .set_starting_offsets(KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.LATEST))
              .set_value_only_deserializer(SimpleStringSchema())
              .build())
    # 출력 행: (k = 경기 키, v = live.metrics 메시지 JSON)
    out_type = Types.ROW_NAMED(["k", "v"], [Types.STRING(), Types.STRING()])
    stored = (env.from_source(source, WatermarkStrategy.no_watermarks(), "kovo.live")
              .map(parse, output_type=Types.STRING()).name("parse")
              .filter(lambda v: v is not None).name("valid")
              .key_by(lambda v: json.loads(v)["match"], key_type=Types.STRING())
              .process(Follow(), output_type=Types.STRING()).name("follow")
              .map(Store(), output_type=out_type).name("store")
              .filter(lambda r: r[1] is not None).name("known-match"))
    stored.map(log_line, output_type=Types.STRING()).name("log").print()

    # SQL Kafka 커넥터로 경기 키를 메시지 키로 붙여 쓴다(여기서는 DataStream KafkaSink로 키를 지정할 수 없어서)
    # 같은 경기 메시지가 같은 파티션으로 가야 SSE 게이트웨이가 순서대로 받는다
    t_env.execute_sql(f"""
        CREATE TABLE live_metrics_out (`k` STRING, `v` STRING) WITH (
            'connector' = 'kafka', 'topic' = 'live.metrics', 'properties.bootstrap.servers' = '{KAFKA}',
            'key.format' = 'raw', 'key.fields' = 'k', 'value.format' = 'raw', 'value.fields-include' = 'EXCEPT_KEY',
            'sink.delivery-guarantee' = 'at-least-once')
    """)
    statements = t_env.create_statement_set()
    statements.add_insert("live_metrics_out", t_env.from_data_stream(stored))
    statements.attach_as_datastream()
    env.execute("live-metrics")


if __name__ == "__main__":
    main()
