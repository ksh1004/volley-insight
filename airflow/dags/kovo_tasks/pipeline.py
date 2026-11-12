"""KOVO DAG들이 함께 쓰는 단계들(설계서 1.8): 수집 → HDFS에 보관될 때까지 대기 → Spark로 정제.

Airflow를 import 하지 않게 만들어 두어 로직을 단위 테스트할 수 있다(airflow/tests). DAG 파일은 이 함수들을
@task로 감싸기만 한다.

  collect_*        수집기를 실행(Kafka로 발행)하고, 실행마다 '배치'를 돌려준다: 토픽, run_id, 메시지 수, 시각
  raw_arrived      그 배치들의 메시지가 모두 HDFS /raw/ 아래 마감된 파일에 들어가면 True
  run_curation     spark_jobs/curate_kovo.py를 YARN에 제출(주어진 시즌들)
  collect_news / articles_stored   news_collect DAG: 기사 발행, 그리고 Flink가 저장했는지 확인
  그 밖에: 대상 경기 고르기, DB 조회(확정 경기, 지켜볼 경기, 품질 관문), 실시간 재생, Spark 작업 제출
"""
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")  # 경기 날짜·시각은 모두 한국 시간 기준
WEBHDFS = os.environ.get("WEBHDFS_URL", "http://namenode:9870/webhdfs/v1")
ELASTICSEARCH = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200").rstrip("/")
SPARK_JOBS = os.environ.get("SPARK_JOBS_DIR", "/opt/airflow/spark_jobs")
# Spark 제출 설정: spark_jobs/submit.sh와 같은 값이어야 손으로 돌린 결과와 DAG 결과가 같다
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


# ---------------------------------------------------------------- 시각
def now_kst() -> datetime:
    """지금 한국 시각(테스트에서 바꿔 끼우기 쉽게 함수로 둠)."""
    return datetime.now(KST)


def batch_of(collector, count: int, started: datetime) -> dict:
    """수집기 실행 한 번을 HDFS에서 찾는 데 필요한 정보(raw_arrived가 씀). XCom으로 다음 작업에 넘어간다."""
    return {"topic": collector.topic, "run_id": collector.run_id, "count": count,
            "published_at": started.astimezone(timezone.utc).isoformat()}


# ---------------------------------------------------------------- 대상 고르기
def parse_seasons(text: str) -> list:
    """시즌 범위 문자열을 시즌 코드 목록으로: "001-003, 010" → ["001", "002", "003", "010"]"""
    out = []
    for part in (x.strip() for x in text.split(",") if x.strip()):
        lo, _, hi = part.partition("-")
        out += [f"{n:03d}" for n in range(int(lo), int(hi or lo) + 1)]
    return sorted(set(out))


def parse_games(text: str) -> dict:
    """경기 번호 문자열을 시즌별 목록으로: "023-9, 023-10 22-5" → {"022": [5], "023": [9, 10]}
    (쉼표나 공백으로 구분. 형식이 틀리면 관리자 화면에 보일 한국어 ValueError)."""
    out = {}
    for part in text.replace(",", " ").split():
        season, sep, gnum = part.partition("-")
        if not sep or not season.isdigit() or not gnum.isdigit() or len(season) > 3 or int(gnum) < 1:
            raise ValueError(f"경기 번호 형식이 아닙니다: {part} (예: 023-9)")
        out.setdefault(f"{int(season):03d}", set()).add(int(gnum))
    return {s: sorted(g) for s, g in sorted(out.items())}


def parse_dates(text: str) -> tuple:
    """날짜 문자열을 (시작, 끝)으로: "2026-11-05" → (그날, 그날), "2026-11-01~2026-11-05" → (시작, 끝).
    시작 <= 끝, 31일 이내만 허용."""
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
    """여러 {시즌: [경기번호]}를 합친다(중복 제거, 정렬)."""
    out = {}
    for part in parts:
        for season, gnums in part.items():
            out.setdefault(season, set()).update(gnums)
    return {s: sorted(g) for s, g in sorted(out.items())}


def pick_targets(schedule_items, confirmed: set, since: date, only: set | None = None):
    """수집할 끝난 경기: `since` 이후에 열렸고 아직 CONFIRMED가 아닌 경기(`only`가 있으면 그 경기 번호만)."""
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


# ---------------------------------------------------------------- 수집 (Kafka로 발행)
# client/publisher를 넘기지 않으면 실제 KOVO 클라이언트와 Kafka 발행기를 만든다(테스트는 가짜를 넘김)
def collect_season_list(client=None, publisher=None):
    """시즌 목록을 발행하고 (배치, 현재 시즌 코드, 그 시즌의 경기 리그 코드들)을 돌려준다.
    현재 시즌 = 경기 리그가 하나라도 있는 가장 최근 시즌."""
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
    """한 시즌 일정을 발행하고 (배치, 일정 항목들)을 돌려준다. 항목을 돌려주므로 호출한 쪽이 다시 요청할 필요가 없다."""
    from collectors.kovo_client import KovoClient
    from collectors.kovo_collectors import ScheduleCollector
    from collectors.publisher import KafkaPublisher

    client, publisher = client or KovoClient(), publisher or KafkaPublisher()
    col = ScheduleCollector(client, publisher)
    started = now_kst()
    n = col.run(season_code=season, leagues=leagues)
    return batch_of(col, n, started), col.games


def collect_games(games, client=None, publisher=None):
    """주어진 경기들의 박스스코어와 문자중계를 발행하고, 수집기마다 배치 하나씩(목록)을 돌려준다."""
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


def collect_honors(codes, client=None, publisher=None):
    """선수들의 수상·주요 기록을 발행하고 (배치, KOVO에 없는 선수 코드들)을 돌려준다. 선수 한 명 = 요청 1번."""
    from collectors.kovo_client import KovoClient
    from collectors.kovo_collectors import HonorCollector
    from collectors.publisher import KafkaPublisher

    col = HonorCollector(client or KovoClient(), publisher or KafkaPublisher())
    started = now_kst()
    n = col.run(codes=codes)
    return batch_of(col, n, started), col.skipped


def collect_news(client=None, publisher=None):
    """배구 기사 메타데이터를 발행한다(RSS + 키가 있으면 네이버 API). (배치, URL들, 실패한 피드, URL별 제목)을 돌려준다."""
    from collectors.news import NewsCollector, NewsHttp
    from collectors.publisher import KafkaPublisher

    col = NewsCollector(client or NewsHttp(), publisher or KafkaPublisher())
    started = now_kst()
    n = col.run()
    return batch_of(col, n, started), col.urls, col.failures, col.titles


# ---------------------------------------------------------------- HDFS 도착 확인
def raw_arrived(batches, http=None, now=None) -> bool:
    """각 배치의 메시지가 모두 /raw/topic=.../dt=.../ 아래 마감된(보이는) 파일에 들어갔으면 True.

    raw-archiver는 파일을 약 6분 안에 마감한다(설계서 1.9). 배치를 발행한 시각 이후에 바뀐 파일만 읽고,
    줄에 그 배치의 run_id가 들어 있으면 그 배치의 메시지로 센다. 파일은 WebHDFS(HTTP)로 스트리밍해 읽는다.
    """
    import requests

    http = http or requests.Session()
    now = now or datetime.now(timezone.utc)
    pending = {b["run_id"]: b for b in batches if b["count"] > 0}
    if not pending:
        return True
    found = dict.fromkeys(pending, 0)
    for topic in sorted({b["topic"] for b in pending.values()}):
        runs = {rid: b for rid, b in pending.items() if b["topic"] == topic}
        first = min(datetime.fromisoformat(b["published_at"]) for b in runs.values())
        since_ms = int((first - timedelta(minutes=2)).timestamp() * 1000)  # 시계 차이를 감안해 2분 여유
        needles = {rid: f'"run_id":"{rid}"'.encode() for rid in runs}
        day, last = first.astimezone(KST).date(), now.astimezone(KST).date()
        while day <= last:  # dt는 수집한 날(한국 날짜). 자정을 넘겼을 수 있어 그날부터 오늘까지 본다
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
    """폴더 안의 마감된 파일 중 since_ms 이후에 바뀐 것의 경로들(폴더가 없으면 [])."""
    resp = http.get(f"{WEBHDFS}{directory}", params={"op": "LISTSTATUS", "user.name": "airflow"}, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return [f"{directory}/{st['pathSuffix']}" for st in resp.json()["FileStatuses"]["FileStatus"]
            if st["type"] == "FILE" and not st["pathSuffix"].startswith((".", "_"))  # 숨김 파일 = 아직 쓰는 중
            and st["modificationTime"] >= since_ms]


# ---------------------------------------------------------------- 서비스 DB 조회
def db_connect():
    """서비스 DB(PostgreSQL) 접속."""
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                            port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"),
                            user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"])


# 경기를 시즌 코드로 찾을 때 쓰는 공통 FROM 절
MATCHES = """
    FROM match m JOIN competition c ON c.id = m.competition_id JOIN season s ON s.id = c.season_id
"""


def confirmed_game_nos(season: str) -> set:
    """그 시즌의 CONFIRMED(확정) 경기 번호들. 이 경기들은 다시 수집하지 않는다."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT m.game_no {MATCHES} WHERE s.code = %s AND m.status = 'CONFIRMED'", (season,))
        return {r[0] for r in cur.fetchall()}


HONOR_RECENT_DAYS = 400  # "현역"으로 볼 기간: 이 안에 경기 기록이 있는 선수(지난 시즌 선수까지 들어간다)


def honor_targets(everyone: bool) -> list:
    """수상 기록을 수집할 선수 코드들. everyone이면 DB의 모든 선수, 아니면 최근 HONOR_RECENT_DAYS일 안에 뛴 선수."""
    where = "" if everyone else "WHERE last_seen_on >= current_date - %s"
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT code FROM player {where} ORDER BY code", () if everyone else (HONOR_RECENT_DAYS,))
        return [r[0] for r in cur.fetchall()]


def games_to_watch(now: datetime) -> dict:
    """어제 이후 예정됐고 이미 시작했지만 아직 CONFIRMED가 아닌 경기, 시즌별: {"023": [5, 6]}."""
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
    """이 날짜들(한국 날짜)에 예정된 경기(리그·상태 무관), 시즌별: {"023": [9, 10]}."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT s.code, m.game_no {MATCHES}
                        WHERE (m.scheduled_at AT TIME ZONE 'Asia/Seoul')::date BETWEEN %s AND %s
                        ORDER BY 1, 2""", (start, end))
        out = {}
        for season, gnum in cur.fetchall():
            out.setdefault(season, []).append(gnum)
        return out


def game_report(games: dict) -> list:
    """주어진 경기들의 (시즌, 경기번호, 예정 시각, 상태). 실행 끝의 로그용."""
    rows = []
    with db_connect() as conn, conn.cursor() as cur:
        for season, gnums in games.items():
            cur.execute(f"""SELECT s.code, m.game_no, m.scheduled_at, m.status {MATCHES}
                            WHERE s.code = %s AND m.game_no = ANY(%s) ORDER BY m.game_no""", (season, list(gnums)))
            rows += cur.fetchall()
    return rows


def match_report(season: str, since: date) -> list:
    """`since` 이후 지금까지 열린 경기들의 상태. 실행 끝의 로그용."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT m.game_no, m.scheduled_at, m.status {MATCHES}
                        WHERE s.code = %s AND m.scheduled_at >= %s AND m.scheduled_at <= now()
                        ORDER BY m.scheduled_at""", (season, since))
        return cur.fetchall()


# ---------------------------------------------------------------- 데이터 품질(PIPE-10)과 보정(ADM-03)
# 정규리그 경기가 시작하고 이만큼 지나도 결과가 없으면 수집이 밀린 것으로 본다.
STALE_AFTER_HOURS = 6


def quality_report(since: datetime, now: datetime) -> dict:
    """품질 관문이 보는 것들(서비스 DB에서):
      errors  `since` 이후 경기의 OPEN 상태 ERROR 문제(ACKNOWLEDGED는 알려진 빈틈이라 제외)
      stale   `since` 이후 정규리그 경기 중 시작한 지 STALE_AFTER_HOURS가 지났는데 결과가 없는 경기
      open    모든 시즌의 OPEN 문제 수, 심각도별(로그용)"""
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
    """품질 관문을 실패시키는 문제 줄들. 비어 있으면 통과."""
    out = [f"{game} set {set_no or '-'}: {rule} {detail}" for game, set_no, rule, detail in report["errors"]]
    out += [f"{game}: started {STALE_AFTER_HOURS}h+ ago, still {status} (result not collected)"
            for game, status in report["stale"]]
    return out


def corrected_seasons(since: datetime) -> list:
    """`since` 이후 경기 단위 보정이 추가·변경·해제된 시즌들: 다시 정제해야 마트에 반영된다.
    선수·팀 보정은 시즌과 무관한 행이라 추가로 정제할 시즌이 없다. 보정 키의 앞 세 글자가 시즌 코드다."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT left(key, 3) FROM data_correction
                       WHERE updated_at >= %s AND target NOT IN ('player', 'team') ORDER BY 1""", (since,))
        return [r[0] for r in cur.fetchall()]


def title_key(title: str) -> str | None:
    """flink_jobs/article_tags.py의 title_key와 같은 규칙(테스트가 둘이 같은지 지킨다): enricher는 키마다 7일 동안
    기사 하나만 저장하므로, 같은 기사의 두 번째 URL은 저장되지 않고 앞 기사로 합쳐진다."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFC", title or "")
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t)
    t = re.sub(r"\((종합|속보|\d보|상보|영상|포토)[^)]*\)", "", t)
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()
    return t if len(t) >= 10 else None


def resolve_stored(urls, titles, stored_urls, recent) -> dict:
    """수집한 URL마다 그것을 대신하는 저장된 기사: 자기 자신, 또는 제목 키가 같은 기사
    (`recent`: 최근 며칠 저장된 [(url, 제목)]). 둘 다 없으면 아직 저장 안 된 것(결과에 없음)."""
    by_key = {}
    for url, title in recent:
        k = title_key(title)
        if k and k not in by_key:
            by_key[k] = url
    out = {}
    for u in urls:
        if u in stored_urls:
            out[u] = u
        elif (k := title_key(titles.get(u, ""))) and k in by_key:
            out[u] = by_key[k]
    return out


def articles_stored(urls, titles=None) -> tuple[int, int]:
    """(수집한 기사 중 PostgreSQL에 있는 수, Elasticsearch `articles`에 있는 수). 제목이 같아 앞 기사로 합쳐진
    것도 저장된 것으로 센다(2026-11-09: 두 언론사의 같은 기사 때문에 센서가 시간 초과될 때까지 기다린 일이 있었음).
    두 저장소 모두 Flink article-enricher가 쓴다. Elasticsearch 문서 id는 sha1(url)."""
    import hashlib

    import requests

    if not urls:
        return 0, 0
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT url FROM article WHERE url = ANY(%s)", (list(urls),))
        stored = {r[0] for r in cur.fetchall()}
        recent = []
        if len(stored) < len(set(urls)):
            cur.execute("SELECT url, title FROM article WHERE created_at >= now() - interval '8 days'")
            recent = cur.fetchall()
    resolved = resolve_stored(urls, titles or {}, stored, recent)
    sha = {u: hashlib.sha1(u.encode()).hexdigest() for u in set(resolved.values())}
    resp = requests.post(f"{ELASTICSEARCH}/articles/_search", timeout=30,
                         json={"query": {"ids": {"values": list(sha.values())}}, "_source": False, "size": len(sha) or 1})
    in_index = {h["_id"] for h in resp.json()["hits"]["hits"]} if resp.ok else set()
    return len(resolved), sum(1 for s in resolved.values() if sha[s] in in_index)


def replay_match(key: str, speed: float, publisher=None) -> dict:
    """끝난 경기의 문자중계를 kovo.live로 다시 보낸다(PIPE-11). 마지막 랠리를 보낼 때까지 기다린다."""
    from collectors.publisher import KafkaPublisher
    from collectors.replay import MatchReplayer

    replayer = MatchReplayer(publisher or KafkaPublisher())
    sent = replayer.replay(key, speed=speed)
    return {"match": key, "session": replayer.session, "rallies": sent}


def live_state(key: str):
    """live_match_state에 있는 그 경기의 (세션, 상태, 세트 스코어). 없으면 None."""
    season, gnum = key.split("-")
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT st.session, st.status, st.home_sets || ':' || st.away_sets FROM live_match_state st
            JOIN match m ON m.id = st.match_id JOIN competition c ON c.id = m.competition_id
            JOIN season s ON s.id = c.season_id WHERE s.code = %s AND m.game_no = %s
        """, (season, int(gnum)))
        return cur.fetchone()


def run_index_entities():
    """index_entities.py 제출: 검색용 Elasticsearch `entities`(팀·선수)를 통째로 다시 만든다."""
    run_spark_job("index_entities.py")


def run_player_types():
    """player_types.py 제출: K-means 선수 유형과 비슷한 선수(ANL-06), 전체 교체."""
    run_spark_job("player_types.py")


def run_player_ratings():
    """player_ratings.py 제출: 선수 카드의 포지션별 능력치(능력치마다 1~99), 전체 교체."""
    run_spark_job("player_ratings.py")


def run_curate_honors():
    """curate_honors.py 제출: 선수별 수상·주요 기록(COL-04), 전체 교체."""
    run_spark_job("curate_honors.py")


# ---------------------------------------------------------------- Spark
def run_curation(seasons):
    """curate_kovo.py를 YARN에 제출한다(client 모드: 드라이버는 이 Airflow 작업자 안에서 돈다)."""
    seasons = ",".join(sorted(set(seasons)))
    run_spark_job("curate_kovo.py", "--seasons", seasons, name=f"curate-kovo-{seasons}")


AIRFLOW_API = os.environ.get("AIRFLOW_API_URL", "http://localhost:8080").rstrip("/")  # Airflow REST API 주소
CURATING_DAGS = ("kovo_daily", "kovo_reprocess")  # 정제를 하는 DAG들(예측이 이들을 기다린다)


def curation_running(http=None) -> list:
    """CURATING_DAGS 중 대기 중이거나 실행 중인 실행이 있는 DAG들(Airflow REST API로. 작업은 메타데이터 DB를 직접
    읽을 수 없다). 예측은 이 DAG들을 기다려야 최신 결과가 특징에 들어간다(2026-11-06, 11-09: PC가 꺼져 있던 뒤
    ml_daily_predict가 kovo_daily보다 먼저 돌아 놓친 경기가 빠진 일이 있었음)."""
    import requests

    http = http or requests.Session()
    busy = []
    for dag in CURATING_DAGS:
        resp = http.get(f"{AIRFLOW_API}/api/v2/dags/{dag}/dagRuns",
                        params=[("state", "running"), ("state", "queued"), ("limit", "1")], timeout=10)
        resp.raise_for_status()
        if resp.json().get("total_entries", 0):
            busy.append(dag)
    return busy


def run_marts():
    """build_marts.py 제출: Elo와 시즌 요약을 모든 정제 시즌으로 다시 만든다."""
    run_spark_job("build_marts.py", name="build-marts")


def run_spark_job(script, *args, name=None):
    """spark_jobs/의 작업 하나를 YARN에 제출한다(모든 작업이 같은 설정). 공용 적재 모듈 pg_merge.py도 함께 보낸다."""
    from airflow.providers.apache.spark.hooks.spark_submit import SparkSubmitHook

    SparkSubmitHook(conn_id="spark_yarn", conf=SPARK_CONF, jars=SPARK_JARS, name=name or script.removesuffix(".py"),
                    application_args=list(args), py_files=f"{SPARK_JOBS}/pg_merge.py") \
        .submit(f"{SPARK_JOBS}/{script}")
