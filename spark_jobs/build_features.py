"""경기 예측용 특징(feature)을 만든다(ANL-03): 경기마다 한 행, 그 경기 시작 전에 알 수 있던 정보만 쓴다.

  입력  HDFS /curated/kovo/{match, set_score, team_match_stat, player_match_stat}, /marts/kovo/match_elo
        (경기 전 Elo와 선수 Elo 팀 전력), /marts/kovo/player_rating (선수 능력치 카드)
  출력  HDFS /features/kovo/match/  (Parquet. 끝난 경기에는 정답(label)이 있고, 예정 경기에는 없다)

시점 규칙(point-in-time): 팀 값은 모두 '이 경기보다 엄격히 이전' 경기들로만 계산한다(시작 시각 기준 as-of 조인).
그래서 이 행들로 학습한 모델은 맞혀야 할 결과를 미리 보지 못한다(데이터 누출 방지).

특징 (따로 적지 않으면 홈 값 - 원정 값)
  elo_diff, elo_prob        경기 전 Elo 차이, Elo로 본 홈 승리 확률
  pelo_diff, pelo_prob      경기 전 선수 Elo 팀 전력 차이, 그것으로 본 홈 승리 확률(build_marts.py의 player_elo)
  form_win, form_setdiff    최근 5경기: 승률, 평균 세트 득실
  form_pointdiff            최근 5경기: 세트당 점수 득실(승패보다 점수 차가 실력을 더 잘 보여 준다)
  pointdiff                 이번 시즌 지금까지: 세트당 점수 득실
  attack, receive           이번 시즌 지금까지: 공격 성공률 %, 리시브 효율 %
  block, ace, error         이번 시즌 지금까지: 세트당 블로킹, 서브 에이스, 범실
  rest                      직전 경기 후 쉰 날 수(최대 14)
  squad                     선수단 수준: 이번 시즌 앞선 경기에서 이 팀으로 뛴 선수들의 종합 능력치를, 각자 앞 두
                            시즌 중 가장 최근 카드로(이번 시즌 카드는 절대 쓰지 않음) 이번 시즌 출전 세트로 가중 평균
                            (player_ratings.py → /marts/kovo/player_rating)
  squad_known               그 세트 중 앞 시즌 카드가 있는 선수의 비율(새 외국인 선수와 신인은 카드가 없다)
  h2h_rate, h2h_n           최근 2년 이 상대에게 홈 팀이 이긴 비율(보정), 맞대결 수
  postseason, men           상황 표시(포스트시즌 여부, 남자부 여부)
정답
  set_score  0..5 = 홈 기준 3:0, 3:1, 3:2, 2:3, 1:3, 0:3     home_win = set_score < 3

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh build_features.py (평소에는 ml_daily_predict DAG)
"""
import os

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
FEATURES = os.environ.get("FEATURES_PATH", "hdfs://namenode:9000/features/kovo")
FINISHED = ["FINISHED", "CONFIRMED"]
FORM_GAMES = 5  # '최근 폼'에 쓰는 경기 수
H2H_DAYS = 730  # 상대 전적을 보는 기간(2년)
# 팀 경기 기록에서 시즌 누적에 쓰는 항목
BOX = ["attack_success", "attack_attempts", "receive_excellent", "receive_errors", "receive_attempts",
       "block_success", "serve_aces", "errors"]
# (홈 세트, 원정 세트) → 정답 클래스 번호
SET_SCORES = {(3, 0): 0, (3, 1): 1, (3, 2): 2, (2, 3): 3, (1, 3): 4, (0, 3): 5}
# 출력 특징 컬럼(train_model.py의 FEATURE_COLS는 이 중 실제로 모델에 넣는 것들)
FEATURE_COLS = ["elo_diff", "elo_prob", "pelo_diff", "pelo_prob", "form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive",
                "block", "ace", "error", "rest", "h2h_rate", "h2h_n", "postseason", "men", "squad", "squad_known"]
PRIOR_SEASONS = 2  # 선수 카드는 그 뒤 이만큼의 시즌까지 squad 특징에 쓰인다
SQUAD = ["sq_w", "sq_known", "sq_sets"]  # squad / squad_known 계산에 쓰는 경기별 합계 컬럼


def team_log(finished, team_stats, set_scores, squad):
    """(팀, 끝난 경기)마다 한 행: 그 경기를 *마친 뒤*의 팀 상태(최근 폼, 시즌 누적 지표, squad).
    squad: squad_sums()의 (경기, 팀)별 합계. 선수 기록이 없는 경기는 0을 더한다(누적값이 그대로 유지됨).
    이 '경기 후 상태'를 as_of()가 다음 경기의 '경기 전 상태'로 가져다 쓴다."""
    points = set_scores.groupBy("season_code", "game_no").agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    # 경기 한 행을 홈·원정 두 행으로 펼친다(팀 입장에서 이겼나, 세트 득실, 점수 득실)
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
    log = sides.join(box, ["season_code", "game_no", "team"], "left") \
        .join(squad, ["season_code", "game_no", "team"], "left").fillna(0, subset=BOX + SQUAD)

    recent = Window.partitionBy("team").orderBy("ts").rowsBetween(-(FORM_GAMES - 1), 0)  # 이 경기 포함 최근 5경기
    # 이번 시즌 처음부터 이 경기까지(시즌이 바뀌면 다시 0부터 누적)
    season = Window.partitionBy("team", "season_code").orderBy("ts").rowsBetween(Window.unboundedPreceding, 0)
    cum = {c: F.sum(c).over(season) for c in BOX + SQUAD + ["sets", "point_diff"]}
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
        (cum["errors"] / cum["sets"]).alias("error"),
        (cum["sq_w"] / cum["sq_known"]).alias("squad"),  # 지금까지 뛴 선수들 카드의 세트 가중 평균
        (cum["sq_known"] / cum["sq_sets"]).alias("squad_known"))


def as_of(matches, log):
    """경기의 양쪽 팀마다, 시작 시각보다 엄격히 이전의 마지막 팀 상태를 붙인다(시즌 누적 값은 같은 시즌일 때만).
    그다음 홈 - 원정 차이를 특징으로 돌려준다. rest = 마지막 경기 후 지난 날 수."""
    sides = matches.select("season_code", "game_no", "ts",
                           F.explode(F.array(F.struct(F.lit("home").alias("side"), F.col("home_code").alias("team")),
                                             F.struct(F.lit("away").alias("side"), F.col("away_code").alias("team")))).alias("s")) \
        .select("season_code", "game_no", "ts", "s.*")
    joined = sides.join(log, "team").where(F.col("log_ts") < F.col("ts"))  # 이 경기 전 상태들만
    # 그중 가장 최근 하나(= 직전 경기를 마친 뒤의 상태)
    latest = Window.partitionBy("season_code", "game_no", "side").orderBy(F.desc("log_ts"))
    joined = joined.withColumn("_n", F.row_number().over(latest)).where("_n = 1")
    same = F.col("log_season") == F.col("season_code")
    state = joined.select(
        "season_code", "game_no", "side", "form_win", "form_setdiff", "form_pointdiff",
        *[F.when(same, F.col(c)).alias(c)
          for c in ("pointdiff", "attack", "receive", "block", "ace", "error", "squad", "squad_known")],
        F.least(F.datediff("ts", "log_ts"), F.lit(14)).alias("rest"))
    cols = ["form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive", "block", "ace", "error", "rest",
            "squad", "squad_known"]
    home = state.where("side = 'home'").select("season_code", "game_no", *[F.col(c).alias(f"h_{c}") for c in cols])
    away = state.where("side = 'away'").select("season_code", "game_no", *[F.col(c).alias(f"a_{c}") for c in cols])
    out = matches.select("season_code", "game_no").join(home, ["season_code", "game_no"], "left") \
        .join(away, ["season_code", "game_no"], "left")
    # 알 수 없는 값(첫 경기, 새 시즌의 첫 경기)은 "차이 없음"(0)으로 본다
    return out.select("season_code", "game_no",
                      *[F.coalesce(F.col(f"h_{c}") - F.col(f"a_{c}"), F.lit(0.0)).alias(c) for c in cols])


def squad_sums(finished, player_stats, ratings):
    """(경기, 팀)마다: 출전 세트 × 선수의 이전 카드 종합(sq_w), 카드가 있는 선수의 세트(sq_known), 전체 세트(sq_sets).
    이전 카드 = 이번 시즌 앞 PRIOR_SEASONS 시즌 중 가장 최근 시즌의 종합 점수. 이번 시즌 카드는 절대 쓰지 않는다
    (그 카드는 지금 예측하려는 바로 그 경기들로 계산되므로, 쓰면 결과를 미리 보는 셈이다)."""
    season_no = lambda c: F.col(c).cast("int")  # noqa: E731  ("021" → 21, 시즌 간 빼기용)
    played = (player_stats.join(finished.select("season_code", "game_no"), ["season_code", "game_no"])
              .select("season_code", "game_no", F.col("team_code").alias("team"), "player_code",
                      (F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))).alias("sets"))
              .where("sets > 0"))
    cards = ratings.where(F.col("overall").isNotNull()).select(
        F.col("season_code").alias("r_season"), "player_code", F.col("overall").cast("double").alias("overall"))
    pairs = played.select("season_code", "player_code").distinct().join(cards, "player_code") \
        .where((season_no("r_season") < season_no("season_code"))
               & (season_no("r_season") >= season_no("season_code") - PRIOR_SEASONS))
    latest = Window.partitionBy("season_code", "player_code").orderBy(F.desc("r_season"))
    prior = pairs.withColumn("_n", F.row_number().over(latest)).where("_n = 1").select("season_code", "player_code", "overall")
    known = F.col("overall").isNotNull()
    return (played.join(prior, ["season_code", "player_code"], "left")
            .groupBy("season_code", "game_no", "team")
            .agg(F.sum(F.when(known, F.col("sets") * F.col("overall"))).alias("sq_w"),
                 F.sum(F.when(known, F.col("sets"))).alias("sq_known"), F.sum("sets").alias("sq_sets")))


def head_to_head(matches, finished):
    """최근 2년 맞대결: 횟수(h2h_n)와 홈 팀 승률(h2h_rate). 같은 두 팀이면 홈/원정이 바뀐 경기도 센다."""
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
        # 라플라스 보정: (승 + 1) / (경기 + 2). 맞대결이 없으면 0.5, 한두 경기로 0이나 1이 되지 않게
        ((F.coalesce("h2h_wins", F.lit(0)) + 1) / (F.coalesce("h2h_n", F.lit(0)) + 2)).alias("h2h_rate"))


def main():
    """모든 경기(끝난 경기 + 예정 경기)의 특징을 만들어 HDFS에 쓴다. 끝난 경기는 학습에, 예정 경기는 예측에 쓰인다."""
    spark = (SparkSession.builder.appName("build-features")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    # ts = 경기 시작 시각(시각이 없으면 0시). 시점 규칙의 기준이 된다
    matches = spark.read.parquet(f"{CURATED}/match").withColumn(
        "ts", F.to_timestamp(F.concat_ws(" ", "game_date", F.coalesce("start_time", F.lit("00:00"))), "yyyy-MM-dd HH:mm")).cache()
    finished = matches.where(F.col("status").isin(FINISHED))
    squad = squad_sums(finished, spark.read.parquet(f"{CURATED}/player_match_stat"),
                       spark.read.parquet(f"{MARTS}/player_rating"))
    log = team_log(finished, spark.read.parquet(f"{CURATED}/team_match_stat"), spark.read.parquet(f"{CURATED}/set_score"),
                   squad).cache()
    elo = spark.read.parquet(f"{MARTS}/match_elo")

    # "3:1" 같은 세트 스코어 문자열 → 정답 클래스 번호
    label = F.create_map(*[x for (h, a), i in SET_SCORES.items() for x in (F.lit(f"{h}:{a}"), F.lit(i))])
    base = matches.select(
        "season_code", "game_no", "league_code", "gender", "home_code", "away_code", "game_date", "status",
        (F.col("league_code") != "201").cast("double").alias("postseason"),
        (F.col("gender") == "M").cast("double").alias("men"),
        F.when(F.col("status").isin(FINISHED), label[F.concat_ws(":", "home_sets", "away_sets")]).cast("double").alias("set_score"))
    feats = (base.join(elo, ["season_code", "game_no"], "left")
             .withColumn("elo_diff", F.coalesce(F.col("home_elo") - F.col("away_elo"), F.lit(0.0)))
             .withColumn("elo_prob", F.coalesce("elo_home_win_prob", F.lit(0.5)))
             .withColumn("pelo_diff", F.coalesce(F.col("home_pelo") - F.col("away_pelo"), F.lit(0.0)))
             .withColumn("pelo_prob", F.coalesce("pelo_home_win_prob", F.lit(0.5)))
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
