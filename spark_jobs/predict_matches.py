"""`champion`(현재 서비스 중인) 모델로 다가오는 경기를 예측하고(ANL-03), 끝난 경기의 예측을 채점한다(ANL-10).

  입력  HDFS /features/kovo/match/ (build_features.py가 만든 경기별 특징), MLflow 모델 `match-winner@champion`
  출력  PostgreSQL match_prediction (+ model_version), sql/merge_predictions.sql로 병합

아직 시작하지 않았고 7일 안에 열리는 경기만 예측을 받는다(이 기간 제한은 병합 SQL이 적용한다. 경기 전에 다시
돌리면 최신 특징으로 예측이 갱신된다). 경기가 끝나면 예측은 더 바뀌지 않게 고정되고 is_correct(적중 여부)가
채워진다. 그래서 사이트의 적중률은 '진짜 경기 전 예측'만 센다.

모델은 세트 스코어 6가지(3:0 ~ 0:3)의 확률을 낸다. 홈 승리 확률 = 앞의 세 칸(3:0, 3:1, 3:2)의 합.

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh predict_matches.py (평소에는 ml_daily_predict DAG)
"""
import json
import os
from pathlib import Path

import mlflow.spark
from mlflow.tracking import MlflowClient
from pyspark.ml.functions import vector_to_array
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from pg_merge import load_and_merge

FEATURES = os.environ.get("FEATURES_PATH", "hdfs://namenode:9000/features/kovo")
MODEL_NAME = "match-winner"
SET_SCORES = ["3:0", "3:1", "3:2", "2:3", "1:3", "0:3"]  # 모델 출력 확률의 순서(홈 기준 세트 스코어)
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_predictions.sql"


def main():
    """챔피언 모델을 불러 예정·진행 중 경기를 예측하고, 모델 버전 정보와 함께 DB에 병합한다."""
    spark = SparkSession.builder.appName("predict-matches").config("spark.sql.session.timeZone", "Asia/Seoul").getOrCreate()
    client = MlflowClient()
    champ = client.get_model_version_by_alias(MODEL_NAME, "champion")
    run = client.get_run(champ.run_id)
    model = mlflow.spark.load_model(f"models:/{MODEL_NAME}@champion")
    print(f"model {MODEL_NAME} v{champ.version} (run {champ.run_id[:8]})")

    upcoming = spark.read.parquet(f"{FEATURES}/match").where(F.col("status").isin("SCHEDULED", "LIVE"))
    probs = F.col("p")
    best = F.expr("array_position(p, array_max(p))")  # 확률이 가장 큰 칸의 번호(1부터) = 예상 세트 스코어
    pred = model.transform(upcoming).withColumn("p", vector_to_array("probability")).select(
        "season_code", "game_no",
        F.round(probs[0] + probs[1] + probs[2], 4).alias("home_win_prob"),
        F.to_json(F.map_from_arrays(F.array(*[F.lit(s) for s in SET_SCORES]),
                                    F.array(*[F.round(probs[i], 4) for i in range(6)]))).alias("set_score_probs"),
        F.element_at(F.array(*[F.lit(int(s[0])) for s in SET_SCORES]), best.cast("int")).alias("pred_home_sets"),
        F.element_at(F.array(*[F.lit(int(s[2])) for s in SET_SCORES]), best.cast("int")).alias("pred_away_sets"))
    print(f"predicted {pred.count()} upcoming matches")

    # 모델이 실제로 학습된 설정의 시험 성적(final_test_*)을 화면에 보여 줄 지표로 쓴다. 예전 실행에는 test_*만 있다
    m = run.data.metrics
    final = {k.removeprefix("final_"): v for k, v in m.items() if k.startswith("final_test_")}
    metrics = {k: round(v, 4) for k, v in {**{k: v for k, v in m.items() if k.startswith("test_")}, **final}.items()}
    metrics.update({k: round(v, 4) for k, v in m.items() if k.startswith("elo_test_")})
    version = spark.createDataFrame([(MODEL_NAME, int(champ.version), champ.run_id, json.dumps(metrics),
                                      champ.tags.get("train_seasons", ""), champ.tags.get("test_season", ""))],
                                    "name string, version int, run_id string, metrics string, train_seasons string, "
                                    "test_season string")
    for row in load_and_merge(spark, {"model_version": version, "match_prediction": pred}, MERGE_SQL, "merge_predictions"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
