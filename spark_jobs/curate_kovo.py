"""KOVO 원본 응답을 경기 테이블들로 정제한다(설계서 DFD 3.1~3.3, PIPE-04). 데이터 파이프라인의 핵심 배치 작업.

  HDFS /raw/topic=kovo.{schedule,match,rally}/   (수집기 봉투, 한 줄에 JSON 하나)
    → 파싱 + 항목마다 가장 최근 사본만 남기기(다시 수집하면 같은 항목이 여러 번 쌓이므로)
    → 관리자 보정 적용(ADM-03, data_correction 테이블)
    → 품질 검사(PIPE-10: 세트 수·세트 점수 규칙, 문자중계 점수 vs 공식 점수, 박스스코어 합계,
       기록 범위, 필수 값) → staging.quality_issue → quality_issue
    → HDFS /curated/kovo/{테이블}/season_code=XXX/   (Parquet, 다른 Spark 작업들의 입력)
    → PostgreSQL 시즌별 staging 스키마(JDBC) → sql/merge_kovo.sql이 서비스 테이블로 upsert

어떤 원본이 어떤 테이블이 되나:
  일정 (/stat/game-schedule)            → 시즌 기간, competition(대회), team, match, set_score
  경기 상세 (/stat/game-schedule/N)     → player, team_match_stat, player_match_stat, player_attack_stat
  문자중계 (/stat/real-time)            → rally_event (+ 이벤트마다의 누적 점수)

랠리는 grade +1 이벤트를 낸 팀이 이긴다. 득점이 난 랠리에는 +1과 -1 이벤트가 정확히 하나씩 있다.
이 규칙으로 문자중계가 있는 2025-26 시즌 1,042개 세트의 공식 점수가 모두 그대로 재현된다.

용어: '정제(curate)'는 원본을 분석·서비스에 쓰기 좋은 표 형태로 바꾸는 것, 'upsert'는 있으면 갱신·없으면 추가.

실행(batch 프로필, airflow 컨테이너에서. 평소에는 Airflow DAG가 실행):
  spark-submit --master yarn --jars /opt/spark-jars/postgresql.jar \
    /opt/airflow/spark_jobs/curate_kovo.py --seasons 022,023
"""
import argparse
import os
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

RAW = os.environ.get("RAW_PATH", "hdfs://namenode:9000/raw")  # Flink raw-archiver가 쌓은 원본
CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_kovo.sql"

SKIP_LEAGUES = ["200", "801", "901", "903"]  # 경기가 아닌 리그: 시즌 통산, 올스타전, 시범경기, 연습경기
POST_LEAGUES = ["202", "203", "204"]  # 포스트시즌: 플레이오프, 챔피언결정전, 준플레이오프
# KOVO 필드 접두사 → 공격 종류(확인: 모든 선수에서 여섯 종류 시도 수의 합이 전체 공격 시도 `att`와 같다)
ATTACK_TYPES = [("oa", "OPEN"), ("ta", "TIME_DIFF"), ("ma", "MOVING"), ("ba", "BACK"),
                ("sa", "QUICK"), ("csa", "QUICK_OPEN")]
# KOVO 필드 → 우리 컬럼 이름. 팀과 선수 박스스코어가 함께 쓴다
# (att 공격 시도, ats 성공, atf 상대 블로킹에 막힘, ate 범실, st/ss/sf 서브 시도/에이스/범실, bt/bs 블로킹 시도/성공,
#  blkvb 유효 블로킹, blkas 블로킹 어시스트, rt/rs/rf 리시브 시도/정확/실패, dt/ds 디그 시도/성공, sett/sets 세트 시도/성공)
BOX = [("att", "attack_attempts"), ("ats", "attack_success"), ("atf", "attack_blocked"), ("ate", "attack_errors"),
       ("st", "serve_attempts"), ("ss", "serve_aces"), ("sf", "serve_errors"),
       ("bt", "block_attempts"), ("bs", "block_success"), ("blkvb", "block_effective"), ("blkas", "block_assist"),
       ("rt", "receive_attempts"), ("rs", "receive_excellent"), ("rf", "receive_errors"),
       ("dt", "dig_attempts"), ("ds", "dig_success"), ("sett", "set_attempts"), ("sets", "set_success"),
       ("err", "errors"), ("warning", "warnings")]
# KOVO 문자중계는 시즌 020(2023-24)부터 있다(수집기의 PLAY_BY_PLAY_FROM과 같은 값).
# 문자중계 누락은 그 시즌부터만 문제로 본다. "이 시즌엔 없음"으로 자동 판단하지 않고 고정값으로 둔 이유:
# KOVO가 새 시즌 문자중계를 늦게 올려도 누락으로 표시돼 다시 수집되게 하려고.
PLAY_BY_PLAY_FROM = "020"

# 아래는 JSON을 읽을 때 쓰는 명시적 스키마(Spark DDL 문자열). 필요한 필드만 적고 나머지는 무시한다.
# 스키마를 정해 두면 Spark가 파일을 훑어 형식을 추측하지 않아 빠르고, 필드 이름이 바뀌면 바로 드러난다.
ENVELOPE = "endpoint: STRING, params: MAP<STRING, STRING>, run_id: STRING, fetched_at: STRING"
GAME = ("seasonCode: STRING, leagueCode: STRING, leagueName: STRING, round: INT, gender: STRING, gnum: INT, "
        "hcode: STRING, hname: STRING, hsname: STRING, acode: STRING, aname: STRING, asname: STRING, "
        "gdate: STRING, gstime: STRING, getime: STRING, hspoint: INT, aspoint: INT, place: STRING, city: STRING, "
        "spectators: INT, referee: STRING, subreferee: STRING, sptime: STRING, gtv: STRING, result: STRING, "
        + ", ".join(f"hs{n}point: INT, as{n}point: INT, s{n}Ptime: STRING" for n in range(1, 6)))
BOX_FIELDS = ", ".join(f"{k}: INT" for k, _ in BOX)
ATTACK_FIELDS = ", ".join(f"{p}{x}: INT" for p, _ in ATTACK_TYPES for x in "tsfe")
PLAYER = ("tcode: STRING, pcode: STRING, pname: STRING, position: STRING, height: INT, ynCaptain: STRING, "
          + ", ".join(f"ynS{n}: STRING" for n in range(1, 6))
          + f", point: INT, bnum: INT, {BOX_FIELDS}, {ATTACK_FIELDS}")
TEAM = f"tcode: STRING, point: INT, {BOX_FIELDS}"
EVENT = ("rtcode: STRING, rpcode: STRING, rbnum: INT, raction: STRING, raresult: STRING, rgrade: INT, "
         "rindex: INT, rpage: INT")

SEASON_LIST_SCHEMA = (f"STRUCT<{ENVELOPE}, payload: STRUCT<payload: ARRAY<STRUCT<seasonCode: STRING, "
                      f"seasonName: STRING, ryear: STRING, leagues: ARRAY<STRUCT<leagueCode: STRING, "
                      f"leagueName: STRING>>>>>>")
SCHEDULE_SCHEMA = f"STRUCT<{ENVELOPE}, payload: STRUCT<payload: STRUCT<content: ARRAY<STRUCT<{GAME}>>>>>"
DETAIL_SCHEMA = (f"STRUCT<{ENVELOPE}, payload: STRUCT<payload: STRUCT<player: ARRAY<STRUCT<{PLAYER}>>, "
                 f"team: ARRAY<STRUCT<{TEAM}>>>>>")
RALLY_SCHEMA = f"STRUCT<{ENVELOPE}, payload: STRUCT<payload: STRUCT<feed: ARRAY<STRUCT<{EVENT}>>>>>"


# ---------------------------------------------------------------- 읽기
def read_endpoint(spark, topic, endpoint_pattern, schema) -> DataFrame:
    """한 토픽의 원본 줄 중 endpoint가 정규식에 맞는 것만 골라, 명시적 스키마로 파싱한다(모르는 필드는 무시).
    한 토픽에 여러 API 응답이 섞여 있어서(예: kovo.schedule에 시즌 목록과 일정) endpoint로 가른다."""
    lines = spark.read.text(f"{RAW}/topic={topic}")
    return (lines.where(F.get_json_object("value", "$.endpoint").rlike(endpoint_pattern))
            .select(F.from_json("value", schema).alias("m")).select("m.*"))


def latest(df: DataFrame, keys, order="fetched_at") -> DataFrame:
    """keys가 같은 행들 중 order가 가장 큰(기본: 가장 최근에 수집한) 행 하나만 남긴다(중복 제거)."""
    w = Window.partitionBy(*keys).orderBy(F.col(order).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")


def in_seasons(col, seasons):
    """시즌 필터 조건. seasons가 비어 있으면 모든 시즌."""
    return F.lit(True) if not seasons else col.isin(seasons)


def blank_to_null(col):
    """앞뒤 공백을 지우고, 빈 문자열이면 NULL로."""
    return F.when(F.trim(col) == "", None).otherwise(F.trim(col))


# 포지션 코드 변경: 시즌 018까지 KOVO는 L(레프트 = 아웃사이드 히터), C(센터 = 미들 블로커), R(라이트 = 아포짓),
# Li(리베로)를 썼고, 019부터 OH, MB, OP, L(리베로), S(세터)를 쓴다. 옛 코드를 지금 코드로 바꿔 "L"이 언제나 리베로가 되게 한다.
OLD_POSITIONS = {"L": "OH", "C": "MB", "R": "OP", "Li": "L"}
CURRENT_CODES_FROM = "019"


def position_code():
    """선수 포지션을 현재 코드 체계로 돌려주는 컬럼 식(모르면 ''). season_code 컬럼이 있어야 한다."""
    pos = F.coalesce(blank_to_null("position"), F.lit(""))
    old = F.col("season_code") < F.lit(CURRENT_CODES_FROM)
    mapped = F.create_map(*[F.lit(x) for kv in OLD_POSITIONS.items() for x in kv])[pos]
    return F.when(old & mapped.isNotNull(), mapped).otherwise(pos)


# ---------------------------------------------------------------- 테이블 만들기
def build_games(spark, seasons):
    """일정 응답에서 경기 목록(경기마다 최신 1행)을 만든다. n_sets = 치른 세트 수,
    finished = 결과가 있고 3세트 이상 치렀는지(끝난 경기)."""
    raw = read_endpoint(spark, "kovo.schedule", r"^/stat/game-schedule$", SCHEDULE_SCHEMA)
    games = (raw.select("fetched_at", F.col("params")["gcode"].alias("gcode"),
                        F.explode("payload.payload.content").alias("g"))
             .select("fetched_at", "gcode", "g.*")
             .where(~F.col("leagueCode").isin(SKIP_LEAGUES) & in_seasons(F.col("seasonCode"), seasons)))
    games = latest(games, ["seasonCode", "gnum"])
    sets = F.coalesce("hspoint", F.lit(0)) + F.coalesce("aspoint", F.lit(0))
    return (games.withColumn("n_sets", sets)
            .withColumn("finished", (F.trim(F.coalesce("result", F.lit(""))) != "") & (sets >= 3)))


def build_matches(games):
    """match 테이블 행. 끝나지 않은 경기는 세트 수 0, 경기 시간·관중은 NULL. sptime("1:52")을 분으로 바꾼다."""
    hhmm = F.split("sptime", ":")
    return games.select(
        F.col("seasonCode").alias("season_code"), "gcode", F.col("leagueCode").alias("league_code"),
        F.col("gnum").alias("game_no"), "round",
        F.when(F.col("gender") == "1", "M").otherwise("F").alias("gender"),
        F.col("hcode").alias("home_code"), F.col("acode").alias("away_code"),
        F.col("gdate").alias("game_date"), blank_to_null("gstime").alias("start_time"),
        F.coalesce(blank_to_null("place"), F.lit("")).alias("venue"),
        F.coalesce(blank_to_null("city"), F.lit("")).alias("city"),
        F.coalesce(blank_to_null("gtv"), F.lit("")).alias("broadcasters"),
        F.when(F.col("finished"), F.col("hspoint")).otherwise(0).alias("home_sets"),
        F.when(F.col("finished"), F.col("aspoint")).otherwise(0).alias("away_sets"),
        F.when(F.col("finished"), hhmm[0].cast("int") * 60 + hhmm[1].cast("int")).alias("duration_min"),
        F.when(F.col("finished"), F.col("spectators")).alias("spectators"),
        F.coalesce(blank_to_null("referee"), F.lit("")).alias("referee_main"),
        F.coalesce(blank_to_null("subreferee"), F.lit("")).alias("referee_sub"),
        "finished",
    )


def build_set_scores(games):
    """set_score 테이블 행: 경기 한 행의 1~5세트 칸을 세트마다 한 행으로 펼치고, 실제로 치른 세트만 남긴다."""
    per_set = [games.select(F.col("seasonCode").alias("season_code"), F.col("gnum").alias("game_no"),
                            F.lit(n).alias("set_no"), F.col(f"hs{n}point").alias("home_points"),
                            F.col(f"as{n}point").alias("away_points"),
                            F.col(f"s{n}Ptime").cast("int").alias("duration_min"), "finished", "n_sets")
               for n in range(1, 6)]
    out = per_set[0]
    for df in per_set[1:]:
        out = out.unionByName(df)
    return out.where(F.col("finished") & (F.col("set_no") <= F.col("n_sets"))).drop("finished", "n_sets")


def build_teams(games):
    """(시즌, 팀)마다 한 행: 가장 최근 이름, 그 시즌에 가장 많이 쓴 홈 경기장. 홈/원정 양쪽에서 팀을 모은다."""
    sides = [games.select(F.col("seasonCode").alias("season_code"), F.col(f"{s}code").alias("code"),
                          F.col(f"{s}sname").alias("name"), F.col(f"{s}name").alias("full_name"),
                          F.when(F.col("gender") == "1", "M").otherwise("F").alias("gender"),
                          F.col("gdate").alias("game_date"),
                          F.when(F.lit(s == "h"), F.col("place")).alias("arena"),
                          F.when(F.lit(s == "h"), F.col("city")).alias("city"))
             for s in ("h", "a")]
    rows = sides[0].unionByName(sides[1])
    names = latest(rows, ["season_code", "code"], order="game_date").select(
        "season_code", "code", "name", "full_name", "gender", F.col("game_date").alias("last_seen_on"))
    homes = (rows.where(F.col("arena").isNotNull()).groupBy("season_code", "code", "arena", "city").count())
    homes = latest(homes, ["season_code", "code"], order="count").select(
        "season_code", "code", F.col("arena").alias("home_arena"), F.col("city").alias("home_city"))
    return names.join(homes, ["season_code", "code"], "left")


def build_details(spark, seasons):
    """경기 상세 응답(경기마다 최신 1개)을 읽는다. 선수 목록(player)과 팀 목록(team) 배열을 그대로 담고 있다."""
    raw = read_endpoint(spark, "kovo.match", r"^/stat/game-schedule/\d+$", DETAIL_SCHEMA)
    raw = raw.select(F.col("params")["seasonCode"].alias("season_code"),
                     F.col("params")["gnum"].cast("int").alias("game_no"), "fetched_at", "payload.payload.*")
    return latest(raw.where(in_seasons(F.col("season_code"), seasons)), ["season_code", "game_no"])


def build_box_scores(details, games):
    """경기 상세에서 선수 기록, 팀 기록, 공격 종류별 기록, 선수 목록 네 가지를 만든다."""
    meta = games.select(F.col("seasonCode").alias("season_code"), F.col("gnum").alias("game_no"),
                        F.col("gdate").alias("game_date"), "gender", "n_sets")
    players = (details.select("season_code", "game_no", F.explode("player").alias("p"))
               .select("season_code", "game_no", "p.*").join(meta, ["season_code", "game_no"]))
    teams = (details.select("season_code", "game_no", F.explode("team").alias("t"))
             .select("season_code", "game_no", "t.*"))
    box = [F.coalesce(F.col(k), F.lit(0)).alias(c) for k, c in BOX]

    # 출전 세트: 세트마다 '1'(뛰었음)/'0'을 이어 붙인 5글자 문자열. KOVO의 ynS{n}이 -2면 그 세트에 안 나왔다는 뜻
    played = F.concat(*[F.when((F.coalesce(f"ynS{n}", F.lit("-2")) != "-2") & (F.lit(n) <= F.col("n_sets")), "1")
                        .otherwise("0") for n in range(1, 6)])
    player_stats = players.select(
        "season_code", "game_no", F.col("pcode").alias("player_code"), F.col("tcode").alias("team_code"),
        F.when(F.col("bnum") > 0, F.col("bnum")).alias("back_number"),
        position_code().alias("position"),
        F.coalesce(F.col("ynCaptain") == "1", F.lit(False)).alias("is_captain"), played.alias("played_sets"),
        F.coalesce("point", F.lit(0)).alias("points"), *box)

    team_stats = teams.select(
        "season_code", "game_no", F.col("tcode").alias("team_code"), F.coalesce("point", F.lit(0)).alias("points"),
        # 상대 범실로 얻은 점수 = 총점 - 공격 - 블로킹 - 서브 득점(KOVO가 따로 주지 않아 계산)
        *box).withColumn("opponent_errors", F.greatest(
            F.lit(0), F.col("points") - F.col("attack_success") - F.col("block_success") - F.col("serve_aces")))

    attacks = None  # 공격 종류마다 한 행으로 펼친다(오픈·시간차·이동·후위·속공·퀵오픈)
    for prefix, kind in ATTACK_TYPES:
        df = players.select("season_code", "game_no", F.col("pcode").alias("player_code"),
                            F.lit(kind).alias("attack_type"),
                            *[F.coalesce(f"{prefix}{x}", F.lit(0)).alias(name)
                              for x, name in zip("tsfe", ["attempts", "success", "blocked", "errors"])])
        attacks = df if attacks is None else attacks.unionByName(df)
    attacks = attacks.where(F.col("attempts") > 0)

    player_rows = players.select(
        "season_code", F.col("pcode").alias("code"), F.col("pname").alias("name"),
        F.when(F.col("gender") == "1", "M").otherwise("F").alias("gender"),
        position_code().alias("position"),
        F.when(F.col("height") > 0, F.col("height")).alias("height_cm"),
        F.col("game_date").alias("last_seen_on"))
    return player_stats, team_stats, attacks, latest(player_rows, ["season_code", "code"], order="last_seen_on")


def build_rally(spark, seasons, games):
    """문자중계를 rally_event 테이블 행으로 만든다: 세트 안 이벤트 순번, 랠리 번호, 팀·선수, 동작·결과·등급,
    그리고 그 이벤트 시점의 누적 점수(+1 이벤트를 팀별로 누적 합산)."""
    raw = read_endpoint(spark, "kovo.rally", r"^/stat/real-time$", RALLY_SCHEMA)
    p = F.col("params")
    pages = raw.select(p["season"].alias("season_code"), p["gnum"].cast("int").alias("game_no"),
                       p["rset"].cast("int").alias("set_no"), p["page"].cast("int").alias("page"),
                       p["hcode"].alias("home_code"), "run_id", "fetched_at",
                       F.col("payload.payload.feed").alias("feed")).where(in_seasons(F.col("season_code"), seasons))
    # 세트마다 '가장 최근 수집 실행'의 페이지들만 쓴다(실행마다 페이지 크기가 달라 페이지끼리 섞으면 안 됨).
    runs = pages.groupBy("season_code", "game_no", "set_no", "run_id").agg(F.max("fetched_at").alias("fetched_at"))
    runs = latest(runs, ["season_code", "game_no", "set_no"]).select("season_code", "game_no", "set_no", "run_id")
    pages = pages.join(runs, ["season_code", "game_no", "set_no", "run_id"])

    events = pages.select("season_code", "game_no", "set_no", "page", "home_code",
                          F.posexplode_outer("feed").alias("pos", "e")).where(F.col("e").isNotNull())
    order = Window.partitionBy("season_code", "game_no", "set_no").orderBy("page", "pos")
    running = order.rowsBetween(Window.unboundedPreceding, Window.currentRow)
    won = F.coalesce(F.col("e.rgrade"), F.lit(0)) > 0
    player = F.col("e.rpcode")
    return events.select(
        "season_code", "game_no", "set_no",
        F.row_number().over(order).alias("event_no"),
        F.col("e.rpage").alias("rally_no"), F.col("e.rindex").alias("seq"),
        blank_to_null("e.rtcode").alias("team_code"),
        F.when(player.rlike(r"^\d{7}$") & (player != "0000000"), player).alias("player_code"),
        F.when(F.col("e.rbnum") > 0, F.col("e.rbnum")).alias("back_number"),
        F.col("e.raction").alias("action_code"), F.coalesce("e.raresult", F.lit("")).alias("result_code"),
        F.coalesce("e.rgrade", F.lit(0)).alias("grade"),
        F.sum(F.when(won & (F.col("e.rtcode") == F.col("home_code")), 1).otherwise(0)).over(running).alias("home_score"),
        F.sum(F.when(won & (F.col("e.rtcode") != F.col("home_code")), 1).otherwise(0)).over(running).alias("away_score"),
    )


# ---------------------------------------------------------------- 관리자 보정(ADM-03)
# 관리자는 Django 관리자 화면(backend/ops, data_correction 테이블)에서 필드 하나씩 고친다. 매 실행은 품질 검사 전에
# 켜져 있는 보정을 모두 적용하므로, 다시 수집해도 보정이 사라지지 않고 마트도 고친 데이터를 읽는다.
CORRECTABLE = {  # Correction.target → (정제 테이블, 키 컬럼들)
    "player": ("player", ["code"]),
    "team": ("team", ["code"]),
    "match": ("match", ["season_code", "game_no"]),
    "set_score": ("set_score", ["season_code", "game_no", "set_no"]),
    "player_match_stat": ("player_match_stat", ["season_code", "game_no", "player_code"]),
    "team_match_stat": ("team_match_stat", ["season_code", "game_no", "team_code"]),
}


def correction_key(target, key):
    """보정 키 문자열을 키 컬럼 값으로 푼다: ("set_score", "023-5/3") → {"season_code": "023", "game_no": 5, "set_no": 3}.
    키 형식이 대상에 맞지 않으면 None(관리자 화면이 이미 검사하지만 한 번 더 막는 안전장치)."""
    cols = CORRECTABLE[target][1]
    if cols == ["code"]:
        return {"code": key.strip()}
    game, _, sub = key.strip().partition("/")
    season, _, game_no = game.partition("-")
    if not (len(season) == 3 and season.isdigit() and game_no.isdigit()) or bool(sub) != (len(cols) == 3):
        return None
    out = {"season_code": season, "game_no": int(game_no)}
    if sub:
        if cols[2] == "set_no" and not sub.isdigit():
            return None
        out[cols[2]] = int(sub) if cols[2] == "set_no" else sub
    return out


def correction_plan(rows):
    """켜진 보정들((대상, 키, 필드, 값))을 {정제 테이블: [(키 컬럼 값, 필드, 값 문자열)]}로 묶는다."""
    plan = {}
    for target, key, field, value in rows:
        if target not in CORRECTABLE:
            continue
        keys = correction_key(target, key)
        if keys is not None:
            plan.setdefault(CORRECTABLE[target][0], []).append((keys, field, value or ""))
    return plan


def load_corrections():
    """PostgreSQL에서 켜진 보정을 읽는다(테이블이나 DB가 없으면 []: 보정 없이도 정제는 돌아야 하므로)."""
    import psycopg2

    try:
        conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                                port=os.environ.get("POSTGRES_PORT", "5432"),
                                dbname=os.environ.get("SERVICE_DB", "service"),
                                user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"])
    except Exception as exc:  # noqa: BLE001 - 보정 없이 돈 실행도 유효한 실행이다
        print(f"corrections: not loaded ({exc.__class__.__name__})")
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.data_correction')")
            if cur.fetchone()[0] is None:
                return []
            cur.execute("SELECT target, key, field, value FROM data_correction WHERE is_active ORDER BY id")
            return cur.fetchall()
    finally:
        conn.close()


def apply_corrections(df, items):
    """보정한 필드를 덮어쓴다(키가 맞는 행만). 숫자 등 텍스트가 아닌 컬럼에 빈 값을 넣으면 NULL로 본다.
    값은 문자열로 저장돼 있어 컬럼 자료형으로 변환(cast)해 넣는다."""
    from pyspark.sql.types import StringType

    for keys, field, text in items or []:
        if field not in df.columns:
            continue  # 예: birth_date는 정제 데이터에 없고 서비스 DB 행에만 있다(그쪽은 merge SQL이 처리)
        dtype = df.schema[field].dataType
        text = text.strip()
        value = text if (text or isinstance(dtype, StringType)) else None
        cond = None
        for col, v in keys.items():
            c = F.col(col) == F.lit(v)
            cond = c if cond is None else cond & c
        df = df.withColumn(field, F.when(cond, F.lit(value).cast(dtype)).otherwise(F.col(field)))
    return df


# ---------------------------------------------------------------- 품질 검사(PIPE-10)
ISSUE_KEYS = ["season_code", "game_no", "set_no", "rule", "subject"]  # 문제 하나를 구별하는 키
# ERROR 규칙에 걸린 경기는 FINISHED에 머물고 CONFIRMED(확정)가 되지 못한다. WARNING은 보고만 한다
BLOCKING = ["SET_COUNT_MISMATCH", "MISSING_BOX_SCORE", "MISSING_RALLY", "RALLY_SCORE_MISMATCH",
            "INVALID_SET_SCORE", "STAT_RANGE", "MISSING_VALUE"]
# 2004-05 ~ 2007-08(시즌 001~004)의 선수 `point`는 약 3분의 1 행에서 공격+블로킹+서브 득점과 다르다(당시 정의가
# 달랐음). 그래서 그 검사는 시즌 005부터 한다.
PLAYER_POINTS_FROM = "005"
# 성공(+ 범실)은 시도보다 많을 수 없다: (이름, 위반 조건 SQL 식)
RANGES = [("attack", "attack_success + attack_blocked + attack_errors > attack_attempts"),
          ("serve", "serve_aces + serve_errors > serve_attempts"),
          ("block", "block_success > block_attempts"),
          ("receive", "receive_excellent + receive_errors > receive_attempts"),
          ("dig", "dig_success > dig_attempts"),
          ("set", "set_success > set_attempts")]


def issue(df, rule, detail, set_no=None, subject=None):
    """문제 행들을 표준 모양(시즌, 경기, 세트, 규칙, 대상, 설명)으로 만든다.
    set_no: 세트 컬럼 이름(없으면 0 = 경기 전체), subject: 대상 컬럼 이름, 또는 "=값"이면 그 글자 그대로."""
    subject_col = F.lit(subject[1:]) if subject and subject.startswith("=") else (F.col(subject) if subject else F.lit(""))
    return df.select("season_code", "game_no",
                     (F.col(set_no) if set_no else F.lit(0)).cast("int").alias("set_no"), F.lit(rule).alias("rule"),
                     subject_col.cast("string").alias("subject"), detail.alias("detail"))


def range_issues(stats, subject):
    """RANGES 규칙 위반 행을 STAT_RANGE 문제로 만든다(어긴 항목 이름들을 설명에 나열)."""
    bad = F.concat_ws(", ", *[F.when(F.expr(expr), F.lit(name)) for name, expr in RANGES])
    return issue(stats.withColumn("bad", bad).where(F.col("bad") != ""), "STAT_RANGE",
                 F.concat(F.lit("more success/errors than attempts: "), F.col("bad")), subject=subject)


def quality_issues(matches, set_scores, team_stats, player_stats, rally):
    """품질 문제 목록: (시즌, 경기, 세트, 규칙, 대상)마다 한 행. set_no 0 = 경기 전체, subject = 팀/선수 코드.
    규칙별로 문제 행을 만든 뒤 모두 합친다. 각 규칙의 뜻은 아래 변수 이름과 detail 문구를 보면 된다."""
    keys = ["season_code", "game_no"]
    finished = matches.where("finished").select(*keys, "home_code", "home_sets", "away_sets")

    missing = F.concat_ws(", ", *[F.when(F.col(c).isNull(), F.lit(c)) for c in ("home_code", "away_code", "game_date")])
    missing_match = issue(matches.withColumn("missing", missing).where(F.col("missing") != ""), "MISSING_VALUE",
                          F.concat(F.lit("schedule row without "), F.col("missing")))
    no_code = player_stats.where(F.col("player_code").isNull() | F.col("team_code").isNull()).groupBy(*keys).count()
    missing_code = issue(no_code, "MISSING_VALUE",
                         F.format_string("%d box-score row(s) without a player or team code", "count"), subject="=box")

    # 세트 점수로 센 승리 세트 수가 경기 결과(세트 스코어)와 같은가
    won = set_scores.groupBy(*keys).agg(
        F.sum(F.when(F.col("home_points") > F.col("away_points"), 1).otherwise(0)).alias("h"),
        F.sum(F.when(F.col("away_points") > F.col("home_points"), 1).otherwise(0)).alias("a"))
    set_count = issue(finished.join(won, keys, "left")
                      .where((F.coalesce("h", F.lit(-1)) != F.col("home_sets")) | (F.coalesce("a", F.lit(-1)) != F.col("away_sets"))),
                      "SET_COUNT_MISMATCH",
                      F.format_string("sets %d:%d, set scores won %s:%s", "home_sets", "away_sets", "h", "a"))

    sets_of_finished = set_scores.join(finished.select(*keys), keys)
    # 세트 점수 규칙: 이긴 팀이 목표 점수(5세트 15, 나머지 25)에 2점 차 이상, 또는 듀스 뒤 정확히 2점 차
    target = F.when(F.col("set_no") == 5, 15).otherwise(25)
    hi, lo = F.greatest("home_points", "away_points"), F.least("home_points", "away_points")
    valid = ((hi == target) & (hi - lo >= 2)) | ((hi > target) & (hi - lo == 2))
    invalid_set = issue(sets_of_finished.where(~F.coalesce(valid, F.lit(False))), "INVALID_SET_SCORE",
                        F.format_string("set score %s:%s", "home_points", "away_points"), set_no="set_no")

    missing_box = issue(finished.join(team_stats.select(*keys).distinct(), keys, "left_anti"),
                        "MISSING_BOX_SCORE", F.lit("no match detail collected"))

    # 문자중계의 마지막 누적 점수가 공식 세트 점수와 같은가(문자중계 시즌만)
    final = rally.groupBy(*keys, "set_no").agg(
        F.max("home_score").alias("rh"), F.max("away_score").alias("ra"), F.count("*").alias("events"))
    checked = sets_of_finished.where(F.col("season_code") >= PLAY_BY_PLAY_FROM).join(final, [*keys, "set_no"], "left")
    missing_rally = issue(checked.where(F.col("events").isNull()), "MISSING_RALLY",
                          F.lit("no play-by-play for this set"), set_no="set_no")
    rally_mismatch = issue(checked.where(F.col("events").isNotNull()
                                         & ((F.col("rh") != F.col("home_points")) | (F.col("ra") != F.col("away_points")))),
                           "RALLY_SCORE_MISMATCH",
                           F.format_string("play-by-play %d:%d, official %d:%d", "rh", "ra", "home_points", "away_points"),
                           set_no="set_no")

    # 팀 합계 = 그 팀 선수 기록의 합인가
    summed = ("attack_success", "block_success", "serve_aces", "dig_success")
    sums = player_stats.groupBy(*keys, "team_code").agg(*[F.sum(c).alias(c) for c in summed])
    box = team_stats.alias("t").join(sums.alias("s"), [*keys, "team_code"])
    box_mismatch = issue(box.where(" OR ".join(f"t.{c} != s.{c}" for c in summed)), "BOX_SCORE_MISMATCH",
                         F.lit("team totals differ from the sum of its players"), subject="team_code")

    # 팀 총점 = 세트 점수의 합인가
    side = set_scores.groupBy(*keys).agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    team_points = (team_stats.join(finished.select(*keys, "home_code"), keys).join(side, keys)
                   .withColumn("set_total", F.when(F.col("team_code") == F.col("home_code"), F.col("hp")).otherwise(F.col("ap"))))
    points_mismatch = issue(team_points.where(F.col("points") != F.col("set_total")), "POINTS_MISMATCH",
                            F.format_string("box score %d, set scores %d", "points", "set_total"), subject="team_code")

    # 선수 득점 = 공격 + 블로킹 + 서브 득점인가
    player_points = issue(player_stats.where((F.col("season_code") >= PLAYER_POINTS_FROM)
                                             & (F.col("points") != F.col("attack_success") + F.col("block_success") + F.col("serve_aces"))),
                          "PLAYER_POINTS_MISMATCH",
                          F.format_string("points %d, attack %d + block %d + ace %d", "points", "attack_success",
                                          "block_success", "serve_aces"), subject="player_code")

    parts = [missing_match, missing_code, set_count, invalid_set, missing_box, missing_rally, rally_mismatch,
             range_issues(player_stats, "player_code"), range_issues(team_stats, "team_code"),
             box_mismatch, points_mismatch, player_points]
    out = parts[0]
    for df in parts[1:]:
        out = out.unionByName(df)
    return finish_issues(out)


def name_issues(players, rally):
    """문자중계에만 나오는 선수는 이름이 없다(관리자가 선수 보정으로 채울 때까지). 이를 경고로 알린다."""
    first = rally.where(F.col("player_code").isNotNull()) \
        .groupBy("season_code", F.col("player_code").alias("code")).agg(F.min("game_no").alias("game_no"))
    nameless = players.where(F.coalesce(F.trim("name"), F.lit("")) == "").join(first, ["season_code", "code"])
    return finish_issues(issue(nameless, "PLAYER_NAME_MISSING", F.lit("only in play-by-play, no name"), subject="code"))


def finish_issues(df):
    """심각도를 붙이고, 키마다 한 행으로 합친다(같은 행에서 한 규칙이 여러 번 걸리면 설명을 '; '로 이어 붙임)."""
    return (df.groupBy(*ISSUE_KEYS).agg(F.concat_ws("; ", F.sort_array(F.collect_list("detail"))).alias("detail"))
            .withColumn("severity", F.when(F.col("rule").isin(BLOCKING), "ERROR").otherwise("WARNING")))


# 예정일이 이만큼 지나도 결과가 없는 경기는 열리지 않은 경기로 본다(예: 챔피언결정전이 3:0으로 끝나 4·5차전이
# 없는 경우). KOVO는 연기된 경기를 새 날짜로 옮기므로, 연기 경기는 SCHEDULED로 남는다.
UNPLAYED_AFTER_DAYS = 2


def with_status(matches, issues):
    """경기 상태를 정한다: 안 끝났고 날짜가 지남 → CANCELLED, 안 끝남 → SCHEDULED,
    끝났고 ERROR 문제 없음 → CONFIRMED(확정), 끝났지만 ERROR 있음 → FINISHED(검증 대기)."""
    blocked = issues.where(F.col("severity") == "ERROR").select("season_code", "game_no").distinct() \
        .withColumn("blocked", F.lit(True))
    past = F.to_date("game_date") < F.date_sub(F.current_date(), UNPLAYED_AFTER_DAYS)
    return (matches.join(blocked, ["season_code", "game_no"], "left")
            .withColumn("status", F.when(~F.col("finished") & past, "CANCELLED")
                        .when(~F.col("finished"), "SCHEDULED")
                        .when(F.col("blocked").isNull(), "CONFIRMED").otherwise("FINISHED"))
            .drop("blocked", "finished"))


# ---------------------------------------------------------------- 시즌 / 대회
def build_seasons(spark, seasons, games):
    """season 테이블 행, competition(시즌 × 대회 × 리그) 테이블 행, 이번에 처리할 시즌 목록을 만든다.
    시즌 기간은 그 시즌 경기 날짜의 처음과 끝. 대회 종류: KOVO컵(CUP), 포스트시즌(POST), 정규리그(REGULAR)."""
    raw = read_endpoint(spark, "kovo.schedule", r"^/stat/season-list$", SEASON_LIST_SCHEMA)
    lists = latest(raw.select(F.col("params")["gcode"].alias("gcode"), "fetched_at", "payload"), ["gcode"])
    rows = lists.select("gcode", F.explode("payload.payload").alias("s")).select("gcode", "s.*")
    rows = rows.where(F.col("gcode") == "001")  # V-리그 시즌만. KOVO컵은 시즌 코드가 따로 있다(추후)
    played = {r.seasonCode for r in games.select("seasonCode").distinct().collect()}
    wanted = sorted(played if not seasons else set(seasons) & played)
    dates = games.groupBy(F.col("seasonCode").alias("code")).agg(
        F.min("gdate").alias("start_date"), F.max("gdate").alias("end_date"))
    season_rows = (rows.where(F.col("seasonCode").isin(wanted))
                   .select(F.col("seasonCode").alias("code"), F.col("seasonName").alias("name"),
                           F.coalesce("ryear", F.lit("")).alias("years"))
                   .join(dates, "code", "left").withColumn("season_code", F.col("code")))

    named = rows.where(F.col("seasonCode").isin(wanted)).select(
        F.col("seasonCode").alias("season_code"), "gcode", F.explode("leagues").alias("l")) \
        .select("season_code", "gcode", F.col("l.leagueCode").alias("league_code"), F.col("l.leagueName").alias("name"))
    used = games.select(F.col("seasonCode").alias("season_code"), "gcode",
                        F.col("leagueCode").alias("league_code"), F.col("leagueName").alias("name"))
    competitions = latest(named.unionByName(used).where(~F.col("league_code").isin(SKIP_LEAGUES)),
                          ["season_code", "gcode", "league_code"], order="name")
    competitions = competitions.withColumn(
        "type", F.when(F.col("gcode") == "002", "CUP").when(F.col("league_code").isin(POST_LEAGUES), "POST")
        .otherwise("REGULAR"))
    return season_rows, competitions, wanted


# ---------------------------------------------------------------- 출력
def write_curated(tables):
    """정제 테이블을 HDFS에 시즌별 폴더(Parquet)로 쓴다. 동적 덮어쓰기라 이번에 처리한 시즌 폴더만 바뀐다."""
    for name, df in tables.items():
        df.write.mode("overwrite").partitionBy("season_code").parquet(f"{CURATED}/{name}")


def main():
    """전체 흐름: 읽기·만들기 → 보정 → 품질 검사·상태 → 문자중계에만 나온 선수 추가 → HDFS 저장 → DB 적재."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", help="comma separated season codes, e.g. 022,023 (default: every collected season)")
    ap.add_argument("--no-load", action="store_true", help="write /curated only, skip PostgreSQL")
    args = ap.parse_args()
    seasons = [s.strip() for s in args.seasons.split(",")] if args.seasons else None

    spark = (SparkSession.builder.appName(f"curate-kovo{'-' + args.seasons if args.seasons else ''}")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionOverwriteMode", "dynamic")  # 이번에 처리한 시즌 폴더만 교체
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")  # "022"를 숫자 22로 바꾸지 않게
             .getOrCreate())

    games = build_games(spark, seasons).cache()
    season_rows, competitions, wanted = build_seasons(spark, seasons, games)
    print(f"seasons: {wanted}")
    matches = build_matches(games)
    set_scores = build_set_scores(games)
    teams = build_teams(games)
    details = build_details(spark, seasons)
    player_stats, team_stats, attacks, players = build_box_scores(details, games)
    rally = build_rally(spark, seasons, games).cache()

    plan = correction_plan(load_corrections())
    print("corrections:", {name: len(items) for name, items in plan.items()} or "none")
    matches = apply_corrections(matches, plan.get("match"))
    set_scores = apply_corrections(set_scores, plan.get("set_score"))
    teams = apply_corrections(teams, plan.get("team"))
    player_stats = apply_corrections(player_stats, plan.get("player_match_stat"))
    # 팀 기록을 고치면 상대 범실 득점도 다시 계산한다
    team_stats = apply_corrections(team_stats, plan.get("team_match_stat")).withColumn("opponent_errors", F.greatest(
        F.lit(0), F.col("points") - F.col("attack_success") - F.col("block_success") - F.col("serve_aces")))

    issues = quality_issues(matches, set_scores, team_stats, player_stats, rally).cache()
    matches = with_status(matches, issues)

    # 문자중계에만 나온 선수(예: 박스스코어 줄이 없는 교체 선수)도 player 행을 만든다(이름은 비워 둠)
    rally_players = (rally.where(F.col("player_code").isNotNull())
                     .join(games.select(F.col("seasonCode").alias("season_code"), F.col("gnum").alias("game_no"),
                                        F.col("gdate").alias("last_seen_on"), "gender"), ["season_code", "game_no"])
                     .select("season_code", F.col("player_code").alias("code"), "last_seen_on", "gender"))
    rally_players = latest(rally_players.join(players.select("season_code", "code"), ["season_code", "code"], "left_anti"),
                           ["season_code", "code"], order="last_seen_on")
    if rally_players.limit(1).count():
        print(f"players seen only in play-by-play: {rally_players.count()} (name filled from the player API later)")
    players = players.unionByName(rally_players.select(
        "season_code", "code", F.lit("").alias("name"),
        F.when(F.col("gender") == "1", "M").otherwise("F").alias("gender"),
        F.lit("").alias("position"), F.lit(None).cast("int").alias("height_cm"), "last_seen_on"))
    players = apply_corrections(players, plan.get("player"))
    issues = issues.unionByName(name_issues(players, rally)).cache()

    tables = {"season": season_rows, "competition": competitions, "team": teams, "player": players,
              "match": matches, "set_score": set_scores, "team_match_stat": team_stats,
              "player_match_stat": player_stats, "player_attack_stat": attacks, "rally_event": rally,
              "quality_issue": issues}
    # 메모리에 올려 둘 만큼 작다. 같은 데이터를 HDFS에도 쓰고 PostgreSQL에도 넣으므로 한 번만 계산하게 캐시한다.
    tables = {name: df.cache() for name, df in tables.items()}
    print("curated rows:", {name: df.count() for name, df in tables.items()})
    write_curated(tables)
    print("quality issues:")
    for r in issues.groupBy("severity", "rule").count().orderBy("severity", "rule").collect():
        print(f"  {r.severity} {r.rule}: {r['count']}")
    for r in issues.where("severity = 'ERROR'").orderBy("season_code", "game_no", "set_no").limit(20).collect():
        print(f"  {r.season_code}-{r.game_no} set {r.set_no}: {r.rule} {r.subject} {r.detail}")

    if not args.no_load:
        from pg_merge import load_and_merge  # 드라이버에서만 쓴다(airflow 컨테이너의 client 모드)

        # 실행마다 자기 staging 스키마에 적재하므로 두 실행이 동시에 돌아도(예: kovo_daily와 kovo_postgame) 서로
        # 덮어쓰지 않는다. 병합은 잠금을 건 한 트랜잭션이라 사이트는 반쯤 병합된 시즌을 보지 않는다.
        for row in load_and_merge(spark, tables, MERGE_SQL, "merge_kovo"):
            print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
