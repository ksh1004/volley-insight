"""live-metrics: live match state and per-set indicators from play-by-play events (PIPE-06, ANL-07, design.md 3.3).

  Kafka kovo.live  ->  key by match  ->  score, sets, serve, run, set indicators, win probability (live_state.py)
                   ->  PostgreSQL live_match_state / live_set_metric / live_rally (upsert)
                   ->  Kafka live.metrics (key = match, e.g. 023-7)  ->  SSE gateway  ->  browser

Input (collectors/collectors/live.py, replay.py): one message per poll or replayed rally,
  payload = {match, set, start, events: [raw KOVO feed events], mode: LIVE|REPLAY, session}
  `start` is the position of the first event in that set's feed, so repeats are recognised and dropped.
State: one JSON dict per match (keyed ValueState, 12 h TTL). A message with another `session` (a new replay of
the same match) starts over and clears that match's rows. Real live games also move match.status
SCHEDULED -> LIVE -> FINISHED (design.md 4.5); replays never touch match.
Restart: the Kafka position and the states are in the checkpoints (submit.sh restores the newest one). Writes are
upserts, so messages replayed after a restart are harmless. The first start reads only new messages (latest).

Run (stream profile; flink-job-keeper keeps it submitted):
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
STATE_HOURS = 12
# league averages (2025-26 regular season) when a team has no season summary yet
DEFAULT_RATES = {"M": (0.692, 0.308), "F": (0.609, 0.390)}  # (side-out, break)
PRIOR_MATCHES = 5  # this season's rates count fully after about this many matches


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def pg_connect():
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def parse(line: str):
    """Envelope -> flat batch JSON (None for anything malformed)."""
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


MATCH_SQL = """
    SELECT m.gender FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE s.code = %s AND m.game_no = %s
"""
RATES_SQL = """
    SELECT t.code, s.code, tss.matches, tss.sideout_rate::float8 / 100, tss.break_rate::float8 / 100
    FROM team_season_summary tss JOIN team t ON t.id = tss.team_id
    JOIN competition c ON c.id = tss.competition_id JOIN season s ON s.id = c.season_id
    WHERE t.code = ANY(%s) AND c.gcode = '001' AND c.league_code = '201' AND s.code <= %s
      AND tss.sideout_rate IS NOT NULL AND tss.break_rate IS NOT NULL
    ORDER BY s.code DESC
"""


def team_rates(rows, team, season, default):
    """(side-out, break) of one team: this season's, shrunk toward last season's (or the league average) while
    only a few matches are played."""
    cur = next(((n, so, br) for code, s, n, so, br in rows if code == team and s == season), None)
    prev = next(((so, br) for code, s, n, so, br in rows if code == team and s < season), None) or default
    if cur is None:
        return prev
    n, so, br = cur
    w = n / (n + PRIOR_MATCHES)
    return w * so + (1 - w) * prev[0], w * br + (1 - w) * prev[1]


def match_rates(conn, batch):
    """Rates of both teams for the win probability, None if the match is unknown."""
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
    """Keeps each match's state and applies every batch of events to it."""

    def open(self, ctx: RuntimeContext):
        desc = ValueStateDescriptor("match", Types.STRING())
        desc.enable_time_to_live(StateTtlConfig.new_builder(Time.hours(STATE_HOURS))
                                 .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
                                 .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                                 .build())
        self.state = ctx.get_state(desc)
        self.conn = None

    def _rates(self, batch):
        try:
            if self.conn is None or self.conn.closed:
                self.conn = pg_connect()
            with self.conn:
                return match_rates(self.conn, batch)
        except Exception as exc:  # no win probability rather than no live score
            print(f"live-metrics: rates unavailable for {batch['match']}: {exc}")
            if self.conn is not None:
                self.conn.close()
            return None

    def process_element(self, value, ctx):
        from live_state import apply_batch, new_state, summary

        batch = json.loads(value)
        raw = self.state.value()
        st = json.loads(raw) if raw else None
        reset = st is None or st["session"] != batch["session"]
        if reset:
            st = new_state(batch["match"], batch["home"], batch["away"], batch["mode"], batch["session"],
                           self._rates(batch))
        before = st["last_seq"]
        points = apply_batch(st, batch["set"], batch["start"], batch["events"])
        if not reset and st["last_seq"] == before:
            return  # nothing new
        self.state.update(json.dumps(st, ensure_ascii=False))
        yield json.dumps({"type": "update", "reset": reset, "state": summary(st), "points": points,
                          "fetched_at": batch["fetched_at"], "processed_at": now_iso()}, ensure_ascii=False)

    def close(self):
        if self.conn is not None:
            self.conn.close()


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
MATCH_ID_SQL = """
    SELECT m.id FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
    WHERE s.code = %s AND m.game_no = %s
"""


class Store(MapFunction):
    """Upserts one update into PostgreSQL (one transaction) and returns Row(match key, live.metrics message)."""

    def open(self, ctx: RuntimeContext):
        self.conn, self.match_ids, self.team_ids = None, {}, {}

    def _pg(self):
        if self.conn is None or self.conn.closed:
            self.conn = pg_connect()
        return self.conn

    def _ids(self, cur, s):
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
        from psycopg2.extras import Json

        msg = json.loads(value)
        s, reset = msg["state"], msg["reset"]
        conn = self._pg()
        try:
            with conn, conn.cursor() as cur:
                match_id = self._ids(cur, s)
                if match_id is None:
                    return Row(s["match"], None)  # not in the serving DB (yet): nothing to store or show
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
                if s["mode"] == "LIVE":  # design.md 4.5: the live pipeline moves a real game to LIVE and FINISHED
                    if s["status"] == "FINISHED":
                        cur.execute("UPDATE match SET status = 'FINISHED', home_sets = %s, away_sets = %s, updated_at = now() "
                                    "WHERE id = %s AND status IN ('SCHEDULED', 'LIVE')",
                                    (s["home_sets"], s["away_sets"], match_id))
                    else:
                        cur.execute("UPDATE match SET status = 'LIVE', home_sets = %s, away_sets = %s, updated_at = now() "
                                    "WHERE id = %s AND status IN ('SCHEDULED', 'LIVE')",
                                    (s["home_sets"], s["away_sets"], match_id))
        except Exception:
            conn.close()  # reconnect on the next element (the job restarts and replays this one)
            raise
        msg["stored_at"] = now_iso()
        return Row(s["match"], json.dumps(msg, ensure_ascii=False))

    def close(self):
        if self.conn is not None:
            self.conn.close()


def log_line(row):
    msg = json.loads(row[1])
    s = msg["state"]
    prob = s["home_win_prob"]
    return (f"live {s['match']} {s['mode']} set {s['current_set']} {s['home_points']}:{s['away_points']} "
            f"sets {s['home_sets']}:{s['away_sets']} {s['status']} points+{len(msg['points'])} "
            f"home_win={prob if prob is not None else '-'} fetched={msg['fetched_at']} stored={msg['stored_at']}")


def main():
    config = Configuration()
    config.set_string("pipeline.name", "live-metrics")
    config.set_string("execution.checkpointing.interval", "30 s")
    config.set_string("state.checkpoints.dir", "hdfs://namenode:9000/flink/checkpoints/live-metrics")
    config.set_string("execution.checkpointing.externalized-checkpoint-retention", "RETAIN_ON_CANCELLATION")
    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.add_python_file(os.path.join(JOBS_DIR, "live_state.py"))
    t_env = StreamTableEnvironment.create(env)

    source = (KafkaSource.builder()
              .set_bootstrap_servers(KAFKA)
              .set_topics("kovo.live")
              .set_group_id("live-metrics")
              # requirements 2.5: live processing starts from the newest data
              .set_starting_offsets(KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.LATEST))
              .set_value_only_deserializer(SimpleStringSchema())
              .build())
    out_type = Types.ROW_NAMED(["k", "v"], [Types.STRING(), Types.STRING()])
    stored = (env.from_source(source, WatermarkStrategy.no_watermarks(), "kovo.live")
              .map(parse, output_type=Types.STRING()).name("parse")
              .filter(lambda v: v is not None).name("valid")
              .key_by(lambda v: json.loads(v)["match"], key_type=Types.STRING())
              .process(Follow(), output_type=Types.STRING()).name("follow")
              .map(Store(), output_type=out_type).name("store")
              .filter(lambda r: r[1] is not None).name("known-match"))
    stored.map(log_line, output_type=Types.STRING()).name("log").print()

    # the SQL Kafka connector writes the match as the message key (DataStream KafkaSink cannot set a key here)
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
