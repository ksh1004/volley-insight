"""Curate raw KOVO responses into match tables (design.md DFD 3.1-3.3, PIPE-04).

  HDFS /raw/topic=kovo.{schedule,match,rally}/   (collector envelopes, one JSON per line)
    -> parse + keep the latest copy of each item (re-collection makes duplicates)
    -> admin corrections (ADM-03, table data_correction)
    -> quality checks (PIPE-10: set counts and set-score rules, play-by-play vs official score, box score totals,
       stat ranges, required values) -> staging.quality_issue -> quality_issue
    -> HDFS /curated/kovo/{table}/season_code=XXX/   (Parquet)
    -> PostgreSQL `staging` schema (JDBC) -> sql/merge_kovo.sql upserts into the serving tables

Which source feeds what:
  schedule (/stat/game-schedule)      -> season dates, competition, team, match, set_score
  match detail (/stat/game-schedule/N) -> player, team_match_stat, player_match_stat, player_attack_stat
  play-by-play (/stat/real-time)       -> rally_event (+ running score)

A rally is won by the team of the event with grade +1; every scored rally has exactly one +1 and one -1
event. This reproduces the official set score for all 1,042 sets of 2025-26 that have play-by-play.

Run (batch profile, from the airflow container):
  spark-submit --master yarn --jars /opt/spark-jars/postgresql.jar \
    /opt/airflow/spark_jobs/curate_kovo.py --seasons 022,023
"""
import argparse
import os
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

RAW = os.environ.get("RAW_PATH", "hdfs://namenode:9000/raw")
CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_kovo.sql"

SKIP_LEAGUES = ["200", "801", "901", "903"]  # season totals, all-star game, exhibition, practice
POST_LEAGUES = ["202", "203", "204"]
# KOVO prefix -> attack type (verified: the six attempts add up to `att` for every player)
ATTACK_TYPES = [("oa", "OPEN"), ("ta", "TIME_DIFF"), ("ma", "MOVING"), ("ba", "BACK"),
                ("sa", "QUICK"), ("csa", "QUICK_OPEN")]
# KOVO field -> column, shared by team and player box scores
BOX = [("att", "attack_attempts"), ("ats", "attack_success"), ("atf", "attack_blocked"), ("ate", "attack_errors"),
       ("st", "serve_attempts"), ("ss", "serve_aces"), ("sf", "serve_errors"),
       ("bt", "block_attempts"), ("bs", "block_success"), ("blkvb", "block_effective"), ("blkas", "block_assist"),
       ("rt", "receive_attempts"), ("rs", "receive_excellent"), ("rf", "receive_errors"),
       ("dt", "dig_attempts"), ("ds", "dig_success"), ("sett", "set_attempts"), ("sets", "set_success"),
       ("err", "errors"), ("warning", "warnings")]
# KOVO has play-by-play from season 020 (2023-24) on (same constant as collectors PLAY_BY_PLAY_FROM).
# Missing play-by-play is only an issue from that season on. A fixed value, not "this season has none",
# so a new season whose play-by-play KOVO publishes late is still flagged and collected again.
PLAY_BY_PLAY_FROM = "020"

ENVELOPE = "endpoint: STRING, params: MAP<STRING, STRING>, run_id: STRING, fetched_at: STRING"
GAME = ("seasonCode: STRING, leagueCode: STRING, leagueName: STRING, round: INT, gender: STRING, gnum: INT, "
        "hcode: STRING, hname: STRING, hsname: STRING, acode: STRING, aname: STRING, asname: STRING, "
        "gdate: STRING, gstime: STRING, getime: STRING, hspoint: INT, aspoint: INT, place: STRING, city: STRING, "
        "spectators: INT, referee: STRING, subreferee: STRING, sptime: STRING, gtv: STRING, result: STRING, "
        + ", ".join(f"hs{n}point: INT, as{n}point: INT, s{n}Ptime: STRING" for n in range(1, 6)))
BOX_FIELDS = ", ".join(f"{k}: INT" for k, _ in BOX)
ATTACK_FIELDS = ", ".join(f"{p}{x}: INT" for p, _ in ATTACK_TYPES for x in "tsfe")
PLAYER = (f"tcode: STRING, pcode: STRING, pname: STRING, position: STRING, height: INT, ynCaptain: STRING, "
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


# ---------------------------------------------------------------- reading
def read_endpoint(spark, topic, endpoint_pattern, schema) -> DataFrame:
    """Lines of one topic whose endpoint matches, parsed with an explicit schema (unknown fields are ignored)."""
    lines = spark.read.text(f"{RAW}/topic={topic}")
    return (lines.where(F.get_json_object("value", "$.endpoint").rlike(endpoint_pattern))
            .select(F.from_json("value", schema).alias("m")).select("m.*"))


def latest(df: DataFrame, keys, order="fetched_at") -> DataFrame:
    w = Window.partitionBy(*keys).orderBy(F.col(order).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")


def in_seasons(col, seasons):
    return F.lit(True) if not seasons else col.isin(seasons)


def blank_to_null(col):
    return F.when(F.trim(col) == "", None).otherwise(F.trim(col))


# Up to season 018 KOVO used L (left = outside hitter), C (centre), R (right = opposite), Li (libero); from 019 on
# OH, MB, OP, L (libero), S. Mapped to the current codes so "L" always means libero.
OLD_POSITIONS = {"L": "OH", "C": "MB", "R": "OP", "Li": "L"}
CURRENT_CODES_FROM = "019"


def position_code():
    """Player position in the current code set ('' when unknown). Needs a season_code column."""
    pos = F.coalesce(blank_to_null("position"), F.lit(""))
    old = F.col("season_code") < F.lit(CURRENT_CODES_FROM)
    mapped = F.create_map(*[F.lit(x) for kv in OLD_POSITIONS.items() for x in kv])[pos]
    return F.when(old & mapped.isNotNull(), mapped).otherwise(pos)


# ---------------------------------------------------------------- building
def build_games(spark, seasons):
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
    """One row per (season, team): latest names, and the most used home arena of that season."""
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
    raw = read_endpoint(spark, "kovo.match", r"^/stat/game-schedule/\d+$", DETAIL_SCHEMA)
    raw = raw.select(F.col("params")["seasonCode"].alias("season_code"),
                     F.col("params")["gnum"].cast("int").alias("game_no"), "fetched_at", "payload.payload.*")
    return latest(raw.where(in_seasons(F.col("season_code"), seasons)), ["season_code", "game_no"])


def build_box_scores(details, games):
    meta = games.select(F.col("seasonCode").alias("season_code"), F.col("gnum").alias("game_no"),
                        F.col("gdate").alias("game_date"), "gender", "n_sets")
    players = (details.select("season_code", "game_no", F.explode("player").alias("p"))
               .select("season_code", "game_no", "p.*").join(meta, ["season_code", "game_no"]))
    teams = (details.select("season_code", "game_no", F.explode("team").alias("t"))
             .select("season_code", "game_no", "t.*"))
    box = [F.coalesce(F.col(k), F.lit(0)).alias(c) for k, c in BOX]

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
        *box).withColumn("opponent_errors", F.greatest(
            F.lit(0), F.col("points") - F.col("attack_success") - F.col("block_success") - F.col("serve_aces")))

    attacks = None
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
    raw = read_endpoint(spark, "kovo.rally", r"^/stat/real-time$", RALLY_SCHEMA)
    p = F.col("params")
    pages = raw.select(p["season"].alias("season_code"), p["gnum"].cast("int").alias("game_no"),
                       p["rset"].cast("int").alias("set_no"), p["page"].cast("int").alias("page"),
                       p["hcode"].alias("home_code"), "run_id", "fetched_at",
                       F.col("payload.payload.feed").alias("feed")).where(in_seasons(F.col("season_code"), seasons))
    # Keep all pages of the most recent run for each set (runs can use different page sizes).
    runs = pages.groupBy("season_code", "game_no", "set_no", "run_id").agg(F.max("fetched_at").alias("fetched_at"))
    runs = latest(runs, ["season_code", "game_no", "set_no"]).select("season_code", "game_no", "set_no", "run_id")
    pages = pages.join(runs, ["season_code", "game_no", "set_no", "run_id"])

    events = pages.select("season_code", "game_no", "set_no", "page", "home_code",
                          F.posexplode_outer("feed").alias("pos", "e")).where(F.col("e").isNotNull())
    order = Window.partitionBy("season_code", "game_no", "set_no").orderBy("page", "pos")
    running = order.rowsBetween(Window.unboundedPreceding, Window.currentRow)
    won = F.coalesce(F.col("e.rgrade"), F.lit(0)) > 0
    player = F.col("e.rpcode")
    events = events.select(
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
    return events


# ---------------------------------------------------------------- corrections (ADM-03)
# Admins correct single fields in Django admin (backend/ops, table data_correction). Every run applies the active
# corrections before the quality checks, so re-collection never undoes them and the marts read corrected data.
CORRECTABLE = {  # Correction.target -> (curated table, key columns)
    "player": ("player", ["code"]),
    "team": ("team", ["code"]),
    "match": ("match", ["season_code", "game_no"]),
    "set_score": ("set_score", ["season_code", "game_no", "set_no"]),
    "player_match_stat": ("player_match_stat", ["season_code", "game_no", "player_code"]),
    "team_match_stat": ("team_match_stat", ["season_code", "game_no", "team_code"]),
}


def correction_key(target, key):
    """Key columns of a correction: ("set_score", "023-5/3") -> {"season_code": "023", "game_no": 5, "set_no": 3}.
    None when the key does not fit the target (the admin form validates keys; this is only a safety net)."""
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
    """{curated table: [(key columns, field, value text)]} from (target, key, field, value) of active corrections."""
    plan = {}
    for target, key, field, value in rows:
        if target not in CORRECTABLE:
            continue
        keys = correction_key(target, key)
        if keys is not None:
            plan.setdefault(CORRECTABLE[target][0], []).append((keys, field, value or ""))
    return plan


def load_corrections():
    """Active corrections from PostgreSQL ([] when the table or the database is not there)."""
    import psycopg2

    try:
        conn = psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"),
                                port=os.environ.get("POSTGRES_PORT", "5432"),
                                dbname=os.environ.get("SERVICE_DB", "service"),
                                user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"])
    except Exception as exc:  # noqa: BLE001 - a run without corrections is still a valid run
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
    """Overwrite the corrected fields; a blank value is NULL for non-text columns."""
    from pyspark.sql.types import StringType

    for keys, field, text in items or []:
        if field not in df.columns:
            continue  # e.g. birth_date: not in the curated data, only in the serving row
        dtype = df.schema[field].dataType
        text = text.strip()
        value = text if (text or isinstance(dtype, StringType)) else None
        cond = None
        for col, v in keys.items():
            c = F.col(col) == F.lit(v)
            cond = c if cond is None else cond & c
        df = df.withColumn(field, F.when(cond, F.lit(value).cast(dtype)).otherwise(F.col(field)))
    return df


# ---------------------------------------------------------------- quality (PIPE-10)
ISSUE_KEYS = ["season_code", "game_no", "set_no", "rule", "subject"]
# ERROR issues keep a finished match from becoming CONFIRMED; WARNING issues are only reported
BLOCKING = ["SET_COUNT_MISMATCH", "MISSING_BOX_SCORE", "MISSING_RALLY", "RALLY_SCORE_MISMATCH",
            "INVALID_SET_SCORE", "STAT_RANGE", "MISSING_VALUE"]
# KOVO's player `point` in 2004-05 ~ 2007-08 (seasons 001-004) differs from attack + block + ace in about a third
# of the rows (a different definition then), so that check starts with season 005.
PLAYER_POINTS_FROM = "005"
# success (+ errors) can never exceed the attempts
RANGES = [("attack", "attack_success + attack_blocked + attack_errors > attack_attempts"),
          ("serve", "serve_aces + serve_errors > serve_attempts"),
          ("block", "block_success > block_attempts"),
          ("receive", "receive_excellent + receive_errors > receive_attempts"),
          ("dig", "dig_success > dig_attempts"),
          ("set", "set_success > set_attempts")]


def issue(df, rule, detail, set_no=None, subject=None):
    subject_col = F.lit(subject[1:]) if subject and subject.startswith("=") else (F.col(subject) if subject else F.lit(""))
    return df.select("season_code", "game_no",
                     (F.col(set_no) if set_no else F.lit(0)).cast("int").alias("set_no"), F.lit(rule).alias("rule"),
                     subject_col.cast("string").alias("subject"), detail.alias("detail"))


def range_issues(stats, subject):
    bad = F.concat_ws(", ", *[F.when(F.expr(expr), F.lit(name)) for name, expr in RANGES])
    return issue(stats.withColumn("bad", bad).where(F.col("bad") != ""), "STAT_RANGE",
                 F.concat(F.lit("more success/errors than attempts: "), F.col("bad")), subject=subject)


def quality_issues(matches, set_scores, team_stats, player_stats, rally):
    """One row per (season, game, set, rule, subject). set_no 0 = the whole match; subject = team/player code."""
    keys = ["season_code", "game_no"]
    finished = matches.where("finished").select(*keys, "home_code", "home_sets", "away_sets")

    missing = F.concat_ws(", ", *[F.when(F.col(c).isNull(), F.lit(c)) for c in ("home_code", "away_code", "game_date")])
    missing_match = issue(matches.withColumn("missing", missing).where(F.col("missing") != ""), "MISSING_VALUE",
                          F.concat(F.lit("schedule row without "), F.col("missing")))
    no_code = player_stats.where(F.col("player_code").isNull() | F.col("team_code").isNull()).groupBy(*keys).count()
    missing_code = issue(no_code, "MISSING_VALUE",
                         F.format_string("%d box-score row(s) without a player or team code", "count"), subject="=box")

    won = set_scores.groupBy(*keys).agg(
        F.sum(F.when(F.col("home_points") > F.col("away_points"), 1).otherwise(0)).alias("h"),
        F.sum(F.when(F.col("away_points") > F.col("home_points"), 1).otherwise(0)).alias("a"))
    set_count = issue(finished.join(won, keys, "left")
                      .where((F.coalesce("h", F.lit(-1)) != F.col("home_sets")) | (F.coalesce("a", F.lit(-1)) != F.col("away_sets"))),
                      "SET_COUNT_MISMATCH",
                      F.format_string("sets %d:%d, set scores won %s:%s", "home_sets", "away_sets", "h", "a"))

    sets_of_finished = set_scores.join(finished.select(*keys), keys)
    target = F.when(F.col("set_no") == 5, 15).otherwise(25)
    hi, lo = F.greatest("home_points", "away_points"), F.least("home_points", "away_points")
    valid = ((hi == target) & (hi - lo >= 2)) | ((hi > target) & (hi - lo == 2))
    invalid_set = issue(sets_of_finished.where(~F.coalesce(valid, F.lit(False))), "INVALID_SET_SCORE",
                        F.format_string("set score %s:%s", "home_points", "away_points"), set_no="set_no")

    missing_box = issue(finished.join(team_stats.select(*keys).distinct(), keys, "left_anti"),
                        "MISSING_BOX_SCORE", F.lit("no match detail collected"))

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

    summed = ("attack_success", "block_success", "serve_aces", "dig_success")
    sums = player_stats.groupBy(*keys, "team_code").agg(*[F.sum(c).alias(c) for c in summed])
    box = team_stats.alias("t").join(sums.alias("s"), [*keys, "team_code"])
    box_mismatch = issue(box.where(" OR ".join(f"t.{c} != s.{c}" for c in summed)), "BOX_SCORE_MISMATCH",
                         F.lit("team totals differ from the sum of its players"), subject="team_code")

    side = set_scores.groupBy(*keys).agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    team_points = (team_stats.join(finished.select(*keys, "home_code"), keys).join(side, keys)
                   .withColumn("set_total", F.when(F.col("team_code") == F.col("home_code"), F.col("hp")).otherwise(F.col("ap"))))
    points_mismatch = issue(team_points.where(F.col("points") != F.col("set_total")), "POINTS_MISMATCH",
                            F.format_string("box score %d, set scores %d", "points", "set_total"), subject="team_code")

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
    """Players known only from play-by-play have no name until an admin fills it in (a player correction)."""
    first = rally.where(F.col("player_code").isNotNull()) \
        .groupBy("season_code", F.col("player_code").alias("code")).agg(F.min("game_no").alias("game_no"))
    nameless = players.where(F.coalesce(F.trim("name"), F.lit("")) == "").join(first, ["season_code", "code"])
    return finish_issues(issue(nameless, "PLAYER_NAME_MISSING", F.lit("only in play-by-play, no name"), subject="code"))


def finish_issues(df):
    """Severity, and one row per key (several findings of one rule on the same row are joined)."""
    return (df.groupBy(*ISSUE_KEYS).agg(F.concat_ws("; ", F.sort_array(F.collect_list("detail"))).alias("detail"))
            .withColumn("severity", F.when(F.col("rule").isin(BLOCKING), "ERROR").otherwise("WARNING")))


# A game still without a result this many days after its date was not played (e.g. the 4th and 5th finals games
# when a series ends 3:0). KOVO moves postponed games to a new date, so those stay SCHEDULED.
UNPLAYED_AFTER_DAYS = 2


def with_status(matches, issues):
    blocked = issues.where(F.col("severity") == "ERROR").select("season_code", "game_no").distinct() \
        .withColumn("blocked", F.lit(True))
    past = F.to_date("game_date") < F.date_sub(F.current_date(), UNPLAYED_AFTER_DAYS)
    return (matches.join(blocked, ["season_code", "game_no"], "left")
            .withColumn("status", F.when(~F.col("finished") & past, "CANCELLED")
                        .when(~F.col("finished"), "SCHEDULED")
                        .when(F.col("blocked").isNull(), "CONFIRMED").otherwise("FINISHED"))
            .drop("blocked", "finished"))


# ---------------------------------------------------------------- seasons / competitions
def build_seasons(spark, seasons, games):
    raw = read_endpoint(spark, "kovo.schedule", r"^/stat/season-list$", SEASON_LIST_SCHEMA)
    lists = latest(raw.select(F.col("params")["gcode"].alias("gcode"), "fetched_at", "payload"), ["gcode"])
    rows = lists.select("gcode", F.explode("payload.payload").alias("s")).select("gcode", "s.*")
    rows = rows.where(F.col("gcode") == "001")  # V-League seasons; KOVO Cup has its own codes (later)
    played = set(r.seasonCode for r in games.select("seasonCode").distinct().collect())
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


# ---------------------------------------------------------------- output
def write_curated(tables):
    for name, df in tables.items():
        df.write.mode("overwrite").partitionBy("season_code").parquet(f"{CURATED}/{name}")


def load_postgres(spark, tables, seasons):
    import psycopg2  # driver side only (client mode in the airflow container)

    host, port = os.environ.get("POSTGRES_HOST", "postgres"), os.environ.get("POSTGRES_PORT", "5432")
    db, user, password = os.environ.get("SERVICE_DB", "service"), os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"]
    # Each run gets its own staging schema, so two runs at once (e.g. kovo_daily and kovo_postgame) never
    # overwrite each other's staging tables. The merge itself takes an advisory lock and runs one at a time.
    app_id = spark.sparkContext.applicationId
    schema = "staging_" + "".join(ch if ch.isalnum() else "_" for ch in app_id.lower())
    connect = lambda: psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)  # noqa: E731
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    conn.close()
    try:
        url = f"jdbc:postgresql://{host}:{port}/{db}?rewriteBatchedInserts=true"
        for name, df in tables.items():
            (df.write.format("jdbc").mode("overwrite")
             .options(url=url, dbtable=f"{schema}.{name}", user=user, password=password,
                      driver="org.postgresql.Driver", batchsize="5000")
             .save())
        sql = MERGE_SQL.read_text(encoding="utf-8").replace("staging.", f"{schema}.")
        conn = connect()
        try:
            with conn, conn.cursor() as cur:  # one transaction: the site never sees a half-merged season
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('merge_kovo'))")
                cur.execute(sql)
                for row in cur.fetchall() if cur.description else []:
                    print("merge:", *row)
        finally:
            conn.close()
    finally:
        conn = connect()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", help="comma separated season codes, e.g. 022,023 (default: every collected season)")
    ap.add_argument("--no-load", action="store_true", help="write /curated only, skip PostgreSQL")
    args = ap.parse_args()
    seasons = [s.strip() for s in args.seasons.split(",")] if args.seasons else None

    spark = (SparkSession.builder.appName(f"curate-kovo{'-' + args.seasons if args.seasons else ''}")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionOverwriteMode", "dynamic")  # replace only these seasons
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")  # keep "022" a string
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
    team_stats = apply_corrections(team_stats, plan.get("team_match_stat")).withColumn("opponent_errors", F.greatest(
        F.lit(0), F.col("points") - F.col("attack_success") - F.col("block_success") - F.col("serve_aces")))

    issues = quality_issues(matches, set_scores, team_stats, player_stats, rally).cache()
    matches = with_status(matches, issues)

    # rally-only players (e.g. a substitute with no box-score line) still get a player row
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
    # Small enough to keep in memory; the same frames are written to HDFS and loaded into PostgreSQL.
    tables = {name: df.cache() for name, df in tables.items()}
    print("curated rows:", {name: df.count() for name, df in tables.items()})
    write_curated(tables)
    print("quality issues:")
    for r in issues.groupBy("severity", "rule").count().orderBy("severity", "rule").collect():
        print(f"  {r.severity} {r.rule}: {r['count']}")
    for r in issues.where("severity = 'ERROR'").orderBy("season_code", "game_no", "set_no").limit(20).collect():
        print(f"  {r.season_code}-{r.game_no} set {r.set_no}: {r.rule} {r.subject} {r.detail}")

    if not args.no_load:
        load_postgres(spark, tables, wanted)
    spark.stop()


if __name__ == "__main__":
    main()
