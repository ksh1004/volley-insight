"""Match prediction features (ANL-03): one row per match, using only what was known before it started.

  inputs   HDFS /curated/kovo/{match, set_score, team_match_stat}, /marts/kovo/match_elo (pre-match Elo)
  output   HDFS /features/kovo/match/  (Parquet; finished matches carry the label, upcoming ones do not)

Point-in-time rule: every team value comes from the team's matches strictly before this match ("as-of"
join on the start time), so a model trained on these rows never sees the result it has to predict.

features (home minus away unless noted)
  elo_diff, elo_prob        pre-match Elo difference and Elo win probability of the home team
  form_win, form_setdiff    last 5 matches: win rate, average set difference
  form_pointdiff            last 5 matches: point difference per set (margins say more than wins)
  pointdiff                 season to date: point difference per set
  attack, receive           season to date: attack success %, receive efficiency %
  block, ace, error         season to date: per set
  rest                      days since the previous match (capped at 14)
  h2h_rate, h2h_n           home team's win rate against this opponent in the last 2 years (smoothed), meetings
  postseason, men           context flags
label
  set_score  0..5 = home 3:0, 3:1, 3:2, 2:3, 1:3, 0:3     home_win = set_score < 3

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh build_features.py
"""
import os

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
FEATURES = os.environ.get("FEATURES_PATH", "hdfs://namenode:9000/features/kovo")
FINISHED = ["FINISHED", "CONFIRMED"]
FORM_GAMES = 5
H2H_DAYS = 730
BOX = ["attack_success", "attack_attempts", "receive_excellent", "receive_errors", "receive_attempts",
       "block_success", "serve_aces", "errors"]
SET_SCORES = {(3, 0): 0, (3, 1): 1, (3, 2): 2, (2, 3): 3, (1, 3): 4, (0, 3): 5}
FEATURE_COLS = ["elo_diff", "elo_prob", "form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive",
                "block", "ace", "error", "rest", "h2h_rate", "h2h_n", "postseason", "men"]


def team_log(finished, team_stats, set_scores):
    """One row per (team, finished match): the team's state *after* that match."""
    points = set_scores.groupBy("season_code", "game_no").agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    sides = finished.join(points, ["season_code", "game_no"], "left").fillna(0, subset=["hp", "ap"]).select(
        "season_code", "game_no", "ts",
        F.explode(F.array(
            F.struct(F.col("home_code").alias("team"), (F.col("home_sets") > F.col("away_sets")).cast("int").alias("won"),
                     (F.col("home_sets") - F.col("away_sets")).alias("set_diff"), (F.col("hp") - F.col("ap")).alias("point_diff")),
            F.struct(F.col("away_code").alias("team"), (F.col("away_sets") > F.col("home_sets")).cast("int").alias("won"),
                     (F.col("away_sets") - F.col("home_sets")).alias("set_diff"), (F.col("ap") - F.col("hp")).alias("point_diff"))
        )).alias("s"),
        (F.col("home_sets") + F.col("away_sets")).alias("sets")).select("season_code", "game_no", "ts", "s.*", "sets")
    box = team_stats.select("season_code", "game_no", F.col("team_code").alias("team"), *BOX)
    log = sides.join(box, ["season_code", "game_no", "team"], "left").fillna(0, subset=BOX)

    recent = Window.partitionBy("team").orderBy("ts").rowsBetween(-(FORM_GAMES - 1), 0)
    season = Window.partitionBy("team", "season_code").orderBy("ts").rowsBetween(Window.unboundedPreceding, 0)
    cum = {c: F.sum(c).over(season) for c in BOX + ["sets", "point_diff"]}
    pct = lambda a, b: F.when(b > 0, 100 * a / b)  # noqa: E731
    return log.select(
        "team", F.col("season_code").alias("log_season"), F.col("ts").alias("log_ts"),
        F.avg("won").over(recent).alias("form_win"), F.avg("set_diff").over(recent).alias("form_setdiff"),
        (F.sum("point_diff").over(recent) / F.sum("sets").over(recent)).alias("form_pointdiff"),
        (cum["point_diff"] / cum["sets"]).alias("pointdiff"),
        pct(cum["attack_success"], cum["attack_attempts"]).alias("attack"),
        pct(cum["receive_excellent"] - cum["receive_errors"], cum["receive_attempts"]).alias("receive"),
        (cum["block_success"] / cum["sets"]).alias("block"),
        (cum["serve_aces"] / cum["sets"]).alias("ace"),
        (cum["errors"] / cum["sets"]).alias("error"))


def as_of(matches, log):
    """For each match side, the team's last state strictly before kick-off (season stats only if same season)."""
    sides = matches.select("season_code", "game_no", "ts",
                           F.explode(F.array(F.struct(F.lit("home").alias("side"), F.col("home_code").alias("team")),
                                             F.struct(F.lit("away").alias("side"), F.col("away_code").alias("team")))).alias("s")) \
        .select("season_code", "game_no", "ts", "s.*")
    joined = sides.join(log, "team").where(F.col("log_ts") < F.col("ts"))
    latest = Window.partitionBy("season_code", "game_no", "side").orderBy(F.desc("log_ts"))
    joined = joined.withColumn("_n", F.row_number().over(latest)).where("_n = 1")
    same = F.col("log_season") == F.col("season_code")
    state = joined.select(
        "season_code", "game_no", "side", "form_win", "form_setdiff", "form_pointdiff",
        *[F.when(same, F.col(c)).alias(c) for c in ("pointdiff", "attack", "receive", "block", "ace", "error")],
        F.least(F.datediff("ts", "log_ts"), F.lit(14)).alias("rest"))
    cols = ["form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive", "block", "ace", "error", "rest"]
    home = state.where("side = 'home'").select("season_code", "game_no", *[F.col(c).alias(f"h_{c}") for c in cols])
    away = state.where("side = 'away'").select("season_code", "game_no", *[F.col(c).alias(f"a_{c}") for c in cols])
    out = matches.select("season_code", "game_no").join(home, ["season_code", "game_no"], "left") \
        .join(away, ["season_code", "game_no"], "left")
    # unknown (first match, new season) counts as "no difference"
    return out.select("season_code", "game_no",
                      *[F.coalesce(F.col(f"h_{c}") - F.col(f"a_{c}"), F.lit(0.0)).alias(c) for c in cols])


def head_to_head(matches, finished):
    prior = finished.select(F.col("ts").alias("p_ts"), F.col("home_code").alias("p_home"), F.col("away_code").alias("p_away"),
                            F.when(F.col("home_sets") > F.col("away_sets"), F.col("home_code"))
                            .otherwise(F.col("away_code")).alias("p_winner"))
    pair = ((F.col("p_home") == F.col("home_code")) & (F.col("p_away") == F.col("away_code"))) | \
           ((F.col("p_home") == F.col("away_code")) & (F.col("p_away") == F.col("home_code")))
    j = matches.select("season_code", "game_no", "ts", "home_code", "away_code").join(prior, pair) \
        .where((F.col("p_ts") < F.col("ts")) & (F.col("p_ts") >= F.col("ts") - F.expr(f"INTERVAL {H2H_DAYS} DAYS")))
    agg = j.groupBy("season_code", "game_no").agg(
        F.count("*").alias("h2h_n"), F.sum((F.col("p_winner") == F.col("home_code")).cast("int")).alias("h2h_wins"))
    return matches.select("season_code", "game_no").join(agg, ["season_code", "game_no"], "left").select(
        "season_code", "game_no", F.coalesce("h2h_n", F.lit(0)).cast("double").alias("h2h_n"),
        # Laplace smoothing: no meetings -> 0.5
        ((F.coalesce("h2h_wins", F.lit(0)) + 1) / (F.coalesce("h2h_n", F.lit(0)) + 2)).alias("h2h_rate"))


def main():
    spark = (SparkSession.builder.appName("build-features")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    matches = spark.read.parquet(f"{CURATED}/match").withColumn(
        "ts", F.to_timestamp(F.concat_ws(" ", "game_date", F.coalesce("start_time", F.lit("00:00"))), "yyyy-MM-dd HH:mm")).cache()
    finished = matches.where(F.col("status").isin(FINISHED))
    log = team_log(finished, spark.read.parquet(f"{CURATED}/team_match_stat"), spark.read.parquet(f"{CURATED}/set_score")).cache()
    elo = spark.read.parquet(f"{MARTS}/match_elo")

    label = F.create_map(*[x for (h, a), i in SET_SCORES.items() for x in (F.lit(f"{h}:{a}"), F.lit(i))])
    base = matches.select(
        "season_code", "game_no", "league_code", "gender", "home_code", "away_code", "game_date", "status",
        (F.col("league_code") != "201").cast("double").alias("postseason"),
        (F.col("gender") == "M").cast("double").alias("men"),
        F.when(F.col("status").isin(FINISHED), label[F.concat_ws(":", "home_sets", "away_sets")]).cast("double").alias("set_score"))
    feats = (base.join(elo, ["season_code", "game_no"], "left")
             .withColumn("elo_diff", F.coalesce(F.col("home_elo") - F.col("away_elo"), F.lit(0.0)))
             .withColumn("elo_prob", F.coalesce("elo_home_win_prob", F.lit(0.5)))
             .join(as_of(matches, log), ["season_code", "game_no"], "left")
             .join(head_to_head(matches, finished), ["season_code", "game_no"], "left")
             .withColumn("home_win", F.when(F.col("set_score").isNotNull(), (F.col("set_score") < 3).cast("double"))))
    feats = feats.select("season_code", "game_no", "league_code", "gender", "home_code", "away_code", "game_date",
                         "status", *FEATURE_COLS, "set_score", "home_win").cache()
    feats.write.mode("overwrite").parquet(f"{FEATURES}/match")

    n = feats.count()
    labeled = feats.where(F.col("set_score").isNotNull()).count()
    print(f"features: {n} matches, {labeled} with a result, {n - labeled} upcoming/unplayed")
    feats.where(F.col("set_score").isNotNull()).select(*[F.round(F.avg(c), 3).alias(c) for c in FEATURE_COLS]).show(truncate=False)
    spark.stop()


if __name__ == "__main__":
    main()
