"""Shared steps of the KOVO DAGs (design.md 1.8): collect -> wait until archived in HDFS -> curate with Spark.

Kept free of Airflow imports so the logic can be unit tested (airflow/tests). DAG files wrap these in tasks.

  collect_*      run the collectors (Kafka) and return a "batch" per collector run: topic, run_id, count, time
  raw_arrived    True once every message of those batches is in a finished file under HDFS /raw/
  run_curation   spark-submit spark_jobs/curate_kovo.py on YARN for the given seasons
  collect_news / articles_stored   news_collect DAG: publish articles, then check Flink stored them
"""
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
WEBHDFS = os.environ.get("WEBHDFS_URL", "http://namenode:9870/webhdfs/v1")
ELASTICSEARCH = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200").rstrip("/")
SPARK_JOBS = os.environ.get("SPARK_JOBS_DIR", "/opt/airflow/spark_jobs")
# Same settings as spark_jobs/submit.sh
SPARK_CONF = {
    "spark.driver.host": "airflow",
    "spark.driver.bindAddress": "0.0.0.0",
    "spark.driver.memory": "1g",
    "spark.executor.instances": "1",
    "spark.executor.cores": "2",
    "spark.executor.memory": "1536m",
    "spark.yarn.am.memory": "512m",
    "spark.sql.shuffle.partitions": "8",
    "spark.executorEnv.PYSPARK_PYTHON": "/usr/local/bin/python3",
    "spark.yarn.appMasterEnv.PYSPARK_PYTHON": "/usr/local/bin/python3",
}
SPARK_JARS = "/opt/spark-jars/postgresql.jar,/opt/spark-jars/elasticsearch-spark.jar"


# ---------------------------------------------------------------- time
def now_kst() -> datetime:
    return datetime.now(KST)


def batch_of(collector, count: int, started: datetime) -> dict:
    """What raw_arrived needs to find one collector run in HDFS."""
    return {"topic": collector.topic, "run_id": collector.run_id, "count": count,
            "published_at": started.astimezone(timezone.utc).isoformat()}


# ---------------------------------------------------------------- target selection
def parse_seasons(text: str) -> list:
    """"001-003, 010" -> ["001", "002", "003", "010"]"""
    out = []
    for part in (x.strip() for x in text.split(",") if x.strip()):
        lo, _, hi = part.partition("-")
        out += [f"{n:03d}" for n in range(int(lo), int(hi or lo) + 1)]
    return sorted(set(out))


def parse_games(text: str) -> dict:
    """"023-9, 023-10 22-5" -> {"022": [5], "023": [9, 10]} (season-game numbers, commas or spaces)."""
    out = {}
    for part in text.replace(",", " ").split():
        season, sep, gnum = part.partition("-")
        if not sep or not season.isdigit() or not gnum.isdigit() or len(season) > 3 or int(gnum) < 1:
            raise ValueError(f"경기 번호 형식이 아닙니다: {part} (예: 023-9)")
        out.setdefault(f"{int(season):03d}", set()).add(int(gnum))
    return {s: sorted(g) for s, g in sorted(out.items())}


def parse_dates(text: str) -> tuple:
    """"2026-11-05" -> (d, d); "2026-11-01~2026-11-05" -> (start, end). At most 31 days, start <= end."""
    lo, sep, hi = text.strip().partition("~")
    try:
        start = date.fromisoformat(lo.strip())
        end = date.fromisoformat(hi.strip()) if sep else start
    except ValueError:
        raise ValueError(f"날짜 형식이 아닙니다: {text} (예: 2026-11-05 또는 2026-11-01~2026-11-05)") from None
    if end < start or (end - start).days > 30:
        raise ValueError(f"날짜 범위는 시작 <= 끝, 31일 이내여야 합니다: {text}")
    return start, end


def merge_games(*parts: dict) -> dict:
    out = {}
    for part in parts:
        for season, gnums in part.items():
            out.setdefault(season, set()).update(gnums)
    return {s: sorted(g) for s, g in sorted(out.items())}


def pick_targets(schedule_items, confirmed: set, since: date, only: set | None = None):
    """Finished games to collect: played on/after `since`, not CONFIRMED yet, optionally limited to `only` game numbers."""
    from collectors.kovo_collectors import Game, is_finished

    out = []
    for item in schedule_items:
        if not is_finished(item) or int(item["gnum"]) in confirmed:
            continue
        if only is not None and int(item["gnum"]) not in only:
            continue
        if date.fromisoformat(item["gdate"]) < since:
            continue
        out.append(Game.from_schedule(item))
    return sorted(out, key=lambda g: g.gnum)


def games_to_dicts(games):
    return [g.__dict__.copy() for g in games]


def games_from_dicts(rows):
    from collectors.kovo_collectors import Game

    return [Game(**r) for r in rows]


# ---------------------------------------------------------------- collecting (Kafka)
def collect_season_list(client=None, publisher=None):
    """Publish the season list; return (batch, current season code, its game leagues)."""
    from collectors.kovo_client import KovoClient
    from collectors.kovo_collectors import SeasonCollector, leagues_of
    from collectors.publisher import KafkaPublisher

    client, publisher = client or KovoClient(), publisher or KafkaPublisher()
    col = SeasonCollector(client, publisher)
    started = now_kst()
    n = col.run(gcode="001")
    body = col.last_body
    seasons = sorted(str(s["seasonCode"]) for s in body.get("payload") or [] if leagues_of(body, s["seasonCode"]))
    current = seasons[-1]
    return batch_of(col, n, started), current, leagues_of(body, current)


def collect_schedule(season: str, leagues=None, client=None, publisher=None):
    """Publish one season's schedule; return (batch, schedule items) so the caller need not request it again."""
    from collectors.kovo_client import KovoClient
    from collectors.kovo_collectors import ScheduleCollector
    from collectors.publisher import KafkaPublisher

    client, publisher = client or KovoClient(), publisher or KafkaPublisher()
    col = ScheduleCollector(client, publisher)
    started = now_kst()
    n = col.run(season_code=season, leagues=leagues)
    return batch_of(col, n, started), col.games


def collect_games(games, client=None, publisher=None):
    """Publish box scores and play-by-play of the given games; return one batch per collector."""
    from collectors.kovo_client import KovoClient
    from collectors.kovo_collectors import MatchCollector, RallyCollector
    from collectors.publisher import KafkaPublisher

    if not games:
        return []
    client, publisher = client or KovoClient(), publisher or KafkaPublisher()
    batches = []
    for cls in (MatchCollector, RallyCollector):
        col = cls(client, publisher)
        started = now_kst()
        n = col.run(games=games)
        batches.append(batch_of(col, n, started))
    return batches


def collect_news(client=None, publisher=None):
    """Publish volleyball article metadata (RSS + Naver API when a key is set); return (batch, urls, failed feeds)."""
    from collectors.news import NewsCollector, NewsHttp
    from collectors.publisher import KafkaPublisher

    col = NewsCollector(client or NewsHttp(), publisher or KafkaPublisher())
    started = now_kst()
    n = col.run()
    return batch_of(col, n, started), col.urls, col.failures


# ---------------------------------------------------------------- HDFS arrival check
def raw_arrived(batches, http=None, now=None) -> bool:
    """True when each batch's messages are all in finished (visible) files under /raw/topic=.../dt=.../.

    raw-archiver finishes a file within about 6 minutes (design.md 1.9). Only files modified after the
    batch was published are read, and a line belongs to a batch when it carries the batch's run_id.
    """
    import requests

    http = http or requests.Session()
    now = now or datetime.now(timezone.utc)
    pending = {b["run_id"]: b for b in batches if b["count"] > 0}
    if not pending:
        return True
    found = {rid: 0 for rid in pending}
    for topic in sorted({b["topic"] for b in pending.values()}):
        runs = {rid: b for rid, b in pending.items() if b["topic"] == topic}
        first = min(datetime.fromisoformat(b["published_at"]) for b in runs.values())
        since_ms = int((first - timedelta(minutes=2)).timestamp() * 1000)
        needles = {rid: f'"run_id":"{rid}"'.encode() for rid in runs}
        day, last = first.astimezone(KST).date(), now.astimezone(KST).date()
        while day <= last:  # dt is the collection day in KST
            for path in _finished_files(http, f"/raw/topic={topic}/dt={day.isoformat()}", since_ms):
                with http.get(f"{WEBHDFS}{path}", params={"op": "OPEN", "user.name": "airflow"},
                              stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        for rid, needle in needles.items():
                            if needle in line:
                                found[rid] += 1
                                break
            day += timedelta(days=1)
    for rid, b in pending.items():
        log.info("%s run %s: %d/%d messages in HDFS", b["topic"], rid[:8], found[rid], b["count"])
    return all(found[rid] >= b["count"] for rid, b in pending.items())


def _finished_files(http, directory, since_ms):
    resp = http.get(f"{WEBHDFS}{directory}", params={"op": "LISTSTATUS", "user.name": "airflow"}, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return [f"{directory}/{st['pathSuffix']}" for st in resp.json()["FileStatuses"]["FileStatus"]
            if st["type"] == "FILE" and not st["pathSuffix"].startswith((".", "_"))  # hidden = still being written
            and st["modificationTime"] >= since_ms]


# ---------------------------------------------------------------- serving DB
def db_connect():
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"),
                            user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"])


MATCHES = """
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
"""


def confirmed_game_nos(season: str) -> set:
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT m.game_no {MATCHES} WHERE s.code = %s AND m.status = 'CONFIRMED'", (season,))
        return {r[0] for r in cur.fetchall()}


def games_to_watch(now: datetime) -> dict:
    """Started but not CONFIRMED games scheduled since yesterday, by season: {"023": [5, 6]}."""
    since = datetime.combine(now.astimezone(KST).date() - timedelta(days=1), datetime.min.time(), KST)
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT s.code, m.game_no {MATCHES}
                        WHERE m.status <> 'CONFIRMED' AND m.scheduled_at <= %s AND m.scheduled_at >= %s
                        ORDER BY 1, 2""", (now, since))
        out = {}
        for season, gnum in cur.fetchall():
            out.setdefault(season, []).append(gnum)
        return out


def games_on_dates(start: date, end: date) -> dict:
    """Games scheduled on these KST days (any league, any status), by season: {"023": [9, 10]}."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT s.code, m.game_no {MATCHES}
                        WHERE (m.scheduled_at AT TIME ZONE 'Asia/Seoul')::date BETWEEN %s AND %s
                        ORDER BY 1, 2""", (start, end))
        out = {}
        for season, gnum in cur.fetchall():
            out.setdefault(season, []).append(gnum)
        return out


def game_report(games: dict) -> list:
    """(season, game_no, scheduled_at, status) of the given games, for the end-of-run log."""
    rows = []
    with db_connect() as conn, conn.cursor() as cur:
        for season, gnums in games.items():
            cur.execute(f"""SELECT s.code, m.game_no, m.scheduled_at, m.status {MATCHES}
                            WHERE s.code = %s AND m.game_no = ANY(%s) ORDER BY m.game_no""", (season, list(gnums)))
            rows += cur.fetchall()
    return rows


def match_report(season: str, since: date) -> list:
    """Status of games played since `since`, for the end-of-run log."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT m.game_no, m.scheduled_at, m.status {MATCHES}
                        WHERE s.code = %s AND m.scheduled_at >= %s AND m.scheduled_at <= now()
                        ORDER BY m.scheduled_at""", (season, since))
        return cur.fetchall()


# ---------------------------------------------------------------- data quality (PIPE-10) and corrections (ADM-03)
# A regular-season game this long after its start without a result means collection is behind.
STALE_AFTER_HOURS = 6


def quality_report(since: datetime, now: datetime) -> dict:
    """What the quality gate looks at, from the serving DB:
      errors  OPEN ERROR issues of games since `since` (ACKNOWLEDGED ones are known gaps and are skipped)
      stale   regular-season games since `since` that started STALE_AFTER_HOURS ago and still have no result
      open    OPEN issues of all seasons by severity (for the log)"""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT qi.season_code || '-' || qi.game_no, qi.set_no, qi.rule, qi.detail
                       FROM quality_issue qi JOIN match m ON m.id = qi.match_id
                       WHERE qi.status = 'OPEN' AND qi.severity = 'ERROR' AND m.scheduled_at >= %s
                       ORDER BY m.scheduled_at, qi.set_no""", (since,))
        errors = cur.fetchall()
        cur.execute(f"""SELECT s.code || '-' || m.game_no, m.status {MATCHES}
                        WHERE c.type = 'REGULAR' AND m.status IN ('SCHEDULED', 'LIVE')
                          AND m.scheduled_at >= %s AND m.scheduled_at < %s
                        ORDER BY m.scheduled_at""", (since, now - timedelta(hours=STALE_AFTER_HOURS)))
        stale = cur.fetchall()
        cur.execute("SELECT severity, count(*) FROM quality_issue WHERE status = 'OPEN' GROUP BY 1")
        open_counts = dict(cur.fetchall())
    return {"errors": errors, "stale": stale, "open": open_counts}


def gate_problems(report: dict) -> list:
    """Lines that make the quality gate fail; empty = pass."""
    out = [f"{game} set {set_no or '-'}: {rule} {detail}" for game, set_no, rule, detail in report["errors"]]
    out += [f"{game}: started {STALE_AFTER_HOURS}h+ ago, still {status} (result not collected)"
            for game, status in report["stale"]]
    return out


def corrected_seasons(since: datetime) -> list:
    """Seasons with a match-level correction added, changed or turned off since `since`: they are curated again
    so the marts pick it up. Player and team corrections need no extra season (those rows are global)."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT left(key, 3) FROM data_correction
                       WHERE updated_at >= %s AND target NOT IN ('player', 'team') ORDER BY 1""", (since,))
        return [r[0] for r in cur.fetchall()]


def articles_stored(urls) -> tuple[int, int]:
    """(how many of these article URLs are in PostgreSQL, how many in Elasticsearch `articles`).
    Flink article-enricher writes both; repeats from earlier runs are already there, so all should appear."""
    import hashlib

    import requests

    if not urls:
        return 0, 0
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM article WHERE url = ANY(%s)", (list(urls),))
        in_db = cur.fetchone()[0]
    ids = [hashlib.sha1(u.encode()).hexdigest() for u in urls]
    resp = requests.post(f"{ELASTICSEARCH}/articles/_count", json={"query": {"ids": {"values": ids}}}, timeout=30)
    in_es = resp.json().get("count", 0) if resp.ok else 0
    return in_db, in_es


def replay_match(key: str, speed: float, publisher=None) -> dict:
    """Re-publish a finished game's play-by-play to kovo.live (PIPE-11). Blocks until the last rally is sent."""
    from collectors.publisher import KafkaPublisher
    from collectors.replay import MatchReplayer

    replayer = MatchReplayer(publisher or KafkaPublisher())
    sent = replayer.replay(key, speed=speed)
    return {"match": key, "session": replayer.session, "rallies": sent}


def live_state(key: str):
    """(session, status, sets) of a match in live_match_state, or None."""
    season, gnum = key.split("-")
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT st.session, st.status, st.home_sets || ':' || st.away_sets FROM live_match_state st
            JOIN match m ON m.id = st.match_id JOIN competition c ON c.id = m.competition_id
            JOIN season s ON s.id = c.season_id WHERE s.code = %s AND m.game_no = %s
        """, (season, int(gnum)))
        return cur.fetchone()


def run_index_entities():
    """spark-submit index_entities.py: Elasticsearch `entities` (teams and players for search) rebuilt in full."""
    run_spark_job("index_entities.py")


def run_player_types():
    """spark-submit player_types.py: K-means player groups and similar players (ANL-06), full refresh."""
    run_spark_job("player_types.py")


# ---------------------------------------------------------------- Spark
def run_curation(seasons):
    """spark-submit curate_kovo.py on YARN (client mode; the driver runs in this Airflow worker)."""
    from airflow.providers.apache.spark.hooks.spark_submit import SparkSubmitHook

    seasons = ",".join(sorted(set(seasons)))
    SparkSubmitHook(conn_id="spark_yarn", conf=SPARK_CONF, jars=SPARK_JARS, name=f"curate-kovo-{seasons}",
                    application_args=["--seasons", seasons]).submit(f"{SPARK_JOBS}/curate_kovo.py")


def run_marts():
    """spark-submit build_marts.py: Elo ratings and season summaries, rebuilt from all curated seasons."""
    from airflow.providers.apache.spark.hooks.spark_submit import SparkSubmitHook

    SparkSubmitHook(conn_id="spark_yarn", conf=SPARK_CONF, jars=SPARK_JARS, name="build-marts") \
        .submit(f"{SPARK_JOBS}/build_marts.py")


def run_spark_job(script, *args, name=None):
    """spark-submit any job in spark_jobs/ on YARN (same settings as the curation job)."""
    from airflow.providers.apache.spark.hooks.spark_submit import SparkSubmitHook

    SparkSubmitHook(conn_id="spark_yarn", conf=SPARK_CONF, jars=SPARK_JARS, name=name or script.removesuffix(".py"),
                    application_args=list(args), py_files=f"{SPARK_JOBS}/pg_merge.py") \
        .submit(f"{SPARK_JOBS}/{script}")
