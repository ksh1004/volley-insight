"""Analysis marts from the curated tables (design.md DFD 3.4-3.5): ANL-01, ANL-02, ANL-05, ANL-11.

  HDFS /curated/kovo/{match,set_score,team_match_stat,player_match_stat,rally_event}  (all seasons)
    -> team_rating            Elo after every finished match
    -> team_season_summary    record, rank, indicators, side-out / break rate per competition
    -> player_season_summary  indicators + percentile within position per competition
    -> HDFS /marts/kovo/{table}/ (Parquet) and PostgreSQL (one transaction, sql/merge_marts.sql)

Recomputed from all seasons on every run (a few seconds of work; Elo depends on the whole history).

Elo: 1500 start; K = 24 x (1, 1.25, 1.5) for a 1-, 2-, 3-set margin; home advantage from the home win rate
of all matches; at a team's first match of a new season its rating moves a third of the way back to 1500.

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh build_marts.py
"""
import json
import math
import os
from pathlib import Path

import psycopg2
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_marts.sql"
FINISHED = ["FINISHED", "CONFIRMED"]
REGULAR = "201"

ELO_START, ELO_K, SEASON_CARRY = 1500.0, 24.0, 2 / 3
MIN_SET_SHARE = 1 / 3  # same qualification rule as the player rankings (backend/league/standings.py)
# indicator -> higher is better? (errors: lower is better)
INDICATORS = {"attack_success_rate": True, "attack_efficiency": True, "receive_efficiency": True,
              "block_per_set": True, "serve_per_set": True, "dig_per_set": True, "set_per_set": True,
              "error_per_set": False}


def read(spark, name):
    return spark.read.parquet(f"{CURATED}/{name}")


# ---------------------------------------------------------------- Elo (driver side: needs match order)
def elo(matches):
    """matches: rows ordered by time with season_code, game_no, gender, home_code, away_code, home_sets,
    away_sets, game_date. Returns (rating rows, summary dict)."""
    rows = list(matches)
    home_rate = sum(r.home_sets > r.away_sets for r in rows) / max(1, len(rows))
    home_adv = -400 * math.log10(1 / home_rate - 1) if 0 < home_rate < 1 else 0.0
    rating, season_of = {}, {}
    out, hits, log_loss = [], 0, 0.0
    for r in rows:
        for t in (r.home_code, r.away_code):
            if t not in rating:
                rating[t] = ELO_START
            elif season_of[t] != r.season_code:  # new season: regress toward the mean
                rating[t] = ELO_START + (rating[t] - ELO_START) * SEASON_CARRY
            season_of[t] = r.season_code
        rh, ra = rating[r.home_code], rating[r.away_code]
        p_home = 1 / (1 + 10 ** ((ra - rh - home_adv) / 400))
        home_won = r.home_sets > r.away_sets
        hits += (p_home >= 0.5) == home_won
        log_loss -= math.log(p_home if home_won else 1 - p_home)
        k = ELO_K * (1 + 0.25 * (abs(r.home_sets - r.away_sets) - 1))
        change = k * ((1.0 if home_won else 0.0) - p_home)
        rating[r.home_code] = rh + change
        rating[r.away_code] = ra - change
        for team, new, delta, p in ((r.home_code, rh + change, change, p_home), (r.away_code, ra - change, -change, 1 - p_home)):
            out.append((r.season_code, r.game_no, team, r.game_date, round(new, 2), round(delta, 2), round(p, 4)))
    n = max(1, len(rows))
    return out, {"matches": len(rows), "home_win_rate": round(home_rate, 4), "home_advantage_elo": round(home_adv, 1),
                 "elo_accuracy": round(hits / n, 4), "elo_log_loss": round(log_loss / n, 4)}


# ---------------------------------------------------------------- side-out / break (ANL-11)
def serve_receive(rally, matches):
    """Per (season, league, team): rallies served / won while serving, received / won while receiving."""
    first_serve = (rally.where(F.col("action_code").isin("VV", "VS"))
                   .groupBy("season_code", "game_no", "set_no", "rally_no")
                   .agg(F.min_by("team_code", "event_no").alias("server")))
    winner = (rally.where(F.col("grade") > 0).groupBy("season_code", "game_no", "set_no", "rally_no")
              .agg(F.first("team_code").alias("winner")))
    rallies = first_serve.join(winner, ["season_code", "game_no", "set_no", "rally_no"]) \
        .join(matches.select("season_code", "game_no", "league_code", "home_code", "away_code"), ["season_code", "game_no"])
    sides = rallies.select("season_code", "league_code", "server", "winner",
                           F.explode(F.array("home_code", "away_code")).alias("team_code"))
    serving = F.col("server") == F.col("team_code")
    won = F.col("winner") == F.col("team_code")
    return sides.groupBy("season_code", "league_code", "team_code").agg(
        F.sum(F.when(serving, 1).otherwise(0)).alias("served"),
        F.sum(F.when(serving & won, 1).otherwise(0)).alias("breaks"),
        F.sum(F.when(~serving, 1).otherwise(0)).alias("received"),
        F.sum(F.when(~serving & won, 1).otherwise(0)).alias("sideouts"))


# ---------------------------------------------------------------- indicators
def rate_columns(prefix=""):
    c = lambda name: F.col(prefix + name)  # noqa: E731
    pct = lambda a, b: F.when(b > 0, F.round(100 * a / b, 2))  # noqa: E731
    per_set = lambda a: F.when(c("sets") > 0, F.round(a / c("sets"), 3))  # noqa: E731
    return [
        pct(c("attack_success"), c("attack_attempts")).alias("attack_success_rate"),
        pct(c("attack_success") - c("attack_blocked") - c("attack_errors"), c("attack_attempts")).alias("attack_efficiency"),
        pct(c("receive_excellent") - c("receive_errors"), c("receive_attempts")).alias("receive_efficiency"),
        per_set(c("block_success")).alias("block_per_set"),
        per_set(c("serve_aces")).alias("serve_per_set"),
        per_set(c("dig_success")).alias("dig_per_set"),
        per_set(c("set_success")).alias("set_per_set"),
        per_set(c("errors")).alias("error_per_set"),
    ]


SUMS = ["attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_aces", "block_success",
        "receive_attempts", "receive_excellent", "receive_errors", "dig_success", "set_success", "errors"]


def team_summaries(matches, sets, team_stats, ratings, sr):
    m = matches.select("season_code", "game_no", "league_code", "home_code", "away_code", "home_sets", "away_sets")
    pts = sets.groupBy("season_code", "game_no").agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    m = m.join(pts, ["season_code", "game_no"], "left")
    sides = m.select("season_code", "league_code", "game_no",
                     F.explode(F.array(
                         F.struct(F.col("home_code").alias("team_code"), F.col("home_sets").alias("ws"), F.col("away_sets").alias("ls"),
                                  F.col("hp").alias("wp"), F.col("ap").alias("lp")),
                         F.struct(F.col("away_code").alias("team_code"), F.col("away_sets").alias("ws"), F.col("home_sets").alias("ls"),
                                  F.col("ap").alias("wp"), F.col("hp").alias("lp")))).alias("s")).select(
        "season_code", "league_code", "game_no", "s.*")
    win_points = (F.when((F.col("ws") == 3) & (F.col("ls") <= 1), 3).when(F.col("ws") == 3, 2)
                  .when((F.col("ws") == 2) & (F.col("ls") == 3), 1).otherwise(0))
    record = sides.groupBy("season_code", "league_code", "team_code").agg(
        F.count("*").alias("matches"), F.sum((F.col("ws") > F.col("ls")).cast("int")).alias("wins"),
        F.sum((F.col("ws") < F.col("ls")).cast("int")).alias("losses"), F.sum(win_points).alias("win_points"),
        F.sum("ws").alias("sets_won"), F.sum("ls").alias("sets_lost"),
        F.sum(F.coalesce("wp", F.lit(0))).alias("points_won"), F.sum(F.coalesce("lp", F.lit(0))).alias("points_lost"))
    # rank inside the regular season (same order as the standings API; head-to-head not applied)
    order = Window.partitionBy("season_code", "league_code", "gender").orderBy(
        F.desc("win_points"), F.desc("wins"),
        F.desc(F.col("sets_won") / F.greatest(F.col("sets_lost"), F.lit(0.0001))),
        F.desc(F.col("points_won") / F.greatest(F.col("points_lost"), F.lit(0.0001))))
    gender = matches.select("season_code", "league_code", F.explode(F.array("home_code", "away_code")).alias("team_code"),
                            "gender").distinct()  # a semi play-off team may only play away
    record = record.join(gender, ["season_code", "league_code", "team_code"], "left") \
        .withColumn("rank", F.when(F.col("league_code") == REGULAR, F.rank().over(order)))

    box = team_stats.join(m.select("season_code", "game_no", "league_code"), ["season_code", "game_no"]) \
        .groupBy("season_code", "league_code", "team_code").agg(*[F.sum(c).alias(c) for c in SUMS])
    box = box.join(record.select("season_code", "league_code", "team_code",
                                 (F.col("sets_won") + F.col("sets_lost")).alias("sets")), ["season_code", "league_code", "team_code"])
    box = box.select("season_code", "league_code", "team_code", *rate_columns())

    last = Window.partitionBy("season_code", "league_code", "team_code").orderBy(F.desc("rated_on"), F.desc("game_no"))
    final = ratings.join(m.select("season_code", "game_no", "league_code"), ["season_code", "game_no"]) \
        .withColumn("_n", F.row_number().over(last)).where("_n = 1") \
        .select("season_code", "league_code", "team_code", F.col("rating").alias("final_rating"))
    sr = sr.select("season_code", "league_code", "team_code",
                   F.when(F.col("received") > 0, F.round(100 * F.col("sideouts") / F.col("received"), 2)).alias("sideout_rate"),
                   F.when(F.col("served") > 0, F.round(100 * F.col("breaks") / F.col("served"), 2)).alias("break_rate"))
    keys = ["season_code", "league_code", "team_code"]
    return record.drop("gender").join(box, keys, "left").join(sr, keys, "left").join(final, keys, "left")


def player_summaries(matches, player_stats):
    m = matches.select("season_code", "game_no", "league_code", "gender", "home_code", "away_code", "home_sets", "away_sets")
    p = player_stats.join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    sets_played = F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))
    agg = p.groupBy("season_code", "league_code", "gender", "player_code", "team_code").agg(
        F.count("*").alias("matches"), F.sum(sets_played).alias("sets"), F.sum("points").alias("points"),
        F.mode("position").alias("position"), *[F.sum(c).alias(c) for c in SUMS])
    team_sets = m.select("season_code", "league_code",
                         F.explode(F.array("home_code", "away_code")).alias("team_code"),
                         (F.col("home_sets") + F.col("away_sets")).alias("s")) \
        .groupBy("season_code", "league_code", "team_code").agg(F.sum("s").alias("team_sets"))
    agg = agg.join(team_sets, ["season_code", "league_code", "team_code"], "left") \
        .withColumn("qualified", F.col("sets") >= F.col("team_sets") * MIN_SET_SHARE)
    agg = agg.select("season_code", "league_code", "gender", "player_code", "team_code",
                     F.coalesce("position", F.lit("")).alias("position"), "matches", "sets", "points", "qualified",
                     *rate_columns())
    # percentile among qualified players of the same competition, gender and position
    peers = Window.partitionBy("season_code", "league_code", "gender", "position", "qualified")
    pct_cols = []
    for name, higher_better in INDICATORS.items():
        w = peers.orderBy(F.col(name).asc() if higher_better else F.col(name).desc())
        pct_cols.append(F.when(F.col("qualified") & F.col(name).isNotNull(),
                               F.round(100 * F.percent_rank().over(w))).cast("int").alias(f"p_{name}"))
    agg = agg.select("*", *pct_cols)
    json_map = F.to_json(F.map_from_arrays(
        F.array(*[F.lit(n) for n in INDICATORS]), F.array(*[F.col(f"p_{n}") for n in INDICATORS])))
    return agg.withColumn("percentiles", F.when(F.col("qualified"), json_map).otherwise(F.lit("{}"))) \
        .drop(*[f"p_{n}" for n in INDICATORS])


# ---------------------------------------------------------------- PostgreSQL
def load(spark, tables):
    host, port = os.environ.get("POSTGRES_HOST", "postgres"), os.environ.get("POSTGRES_PORT", "5432")
    db, user, password = os.environ.get("SERVICE_DB", "service"), os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"]
    connect = lambda: psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)  # noqa: E731
    schema = "staging_" + "".join(ch if ch.isalnum() else "_" for ch in spark.sparkContext.applicationId.lower())
    conn = connect()
    conn.autocommit = True
    conn.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    try:
        url = f"jdbc:postgresql://{host}:{port}/{db}?rewriteBatchedInserts=true"
        for name, df in tables.items():
            df.write.format("jdbc").mode("overwrite").options(
                url=url, dbtable=f"{schema}.{name}", user=user, password=password,
                driver="org.postgresql.Driver", batchsize="5000").save()
        with connect() as tx, tx.cursor() as cur:  # one transaction
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('merge_marts'))")
            cur.execute(MERGE_SQL.read_text(encoding="utf-8").replace("staging.", f"{schema}."))
            for row in cur.fetchall() if cur.description else []:
                print("merge:", *row)
    finally:
        conn.cursor().execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def main():
    spark = (SparkSession.builder.appName("build-marts")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    matches = read(spark, "match").where(F.col("status").isin(FINISHED)).cache()
    sets, team_stats = read(spark, "set_score"), read(spark, "team_match_stat")
    player_stats, rally = read(spark, "player_match_stat"), read(spark, "rally_event")

    rating_rows, info = elo(matches.select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets",
                                           "away_sets", "game_date", "start_time")
                            .orderBy("game_date", F.coalesce("start_time", F.lit("00:00")), "season_code", "game_no")
                            .toLocalIterator())
    print("elo:", json.dumps(info))
    ratings = spark.createDataFrame(rating_rows, "season_code string, game_no int, team_code string, rated_on string, "
                                                 "rating double, rating_change double, win_prob double").cache()

    tables = {
        "team_rating": ratings,
        "team_season_summary": team_summaries(matches, sets, team_stats, ratings, serve_receive(rally, matches)),
        "player_season_summary": player_summaries(matches, player_stats),
    }
    tables = {k: v.cache() for k, v in tables.items()}
    print("rows:", {k: v.count() for k, v in tables.items()})
    for name, df in tables.items():
        df.write.mode("overwrite").parquet(f"{MARTS}/{name}")
    load(spark, tables)
    spark.stop()


if __name__ == "__main__":
    main()
