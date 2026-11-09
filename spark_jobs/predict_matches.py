"""Predict upcoming matches with the `champion` model (ANL-03) and score finished ones (ANL-10).

  input   HDFS /features/kovo/match/ (build_features.py), MLflow model `match-winner@champion`
  output  PostgreSQL match_prediction (+ model_version), via sql/merge_predictions.sql

Only matches that have not started and start within 7 days get a prediction (the merge SQL applies the horizon;
re-running before kick-off refreshes it with the latest features). Once a match is over its prediction is frozen and is_correct is set, so the hit rate on the site
only counts real pre-match predictions.

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh predict_matches.py
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
SET_SCORES = ["3:0", "3:1", "3:2", "2:3", "1:3", "0:3"]
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_predictions.sql"


def main():
    spark = SparkSession.builder.appName("predict-matches").config("spark.sql.session.timeZone", "Asia/Seoul").getOrCreate()
    client = MlflowClient()
    champ = client.get_model_version_by_alias(MODEL_NAME, "champion")
    run = client.get_run(champ.run_id)
    model = mlflow.spark.load_model(f"models:/{MODEL_NAME}@champion")
    print(f"model {MODEL_NAME} v{champ.version} (run {champ.run_id[:8]})")

    upcoming = spark.read.parquet(f"{FEATURES}/match").where(F.col("status").isin("SCHEDULED", "LIVE"))
    probs = F.col("p")
    best = F.expr("array_position(p, array_max(p))")  # 1-based class with the highest probability
    pred = model.transform(upcoming).withColumn("p", vector_to_array("probability")).select(
        "season_code", "game_no",
        F.round(probs[0] + probs[1] + probs[2], 4).alias("home_win_prob"),
        F.to_json(F.map_from_arrays(F.array(*[F.lit(s) for s in SET_SCORES]),
                                    F.array(*[F.round(probs[i], 4) for i in range(6)]))).alias("set_score_probs"),
        F.element_at(F.array(*[F.lit(int(s[0])) for s in SET_SCORES]), best.cast("int")).alias("pred_home_sets"),
        F.element_at(F.array(*[F.lit(int(s[2])) for s in SET_SCORES]), best.cast("int")).alias("pred_away_sets"))
    print(f"predicted {pred.count()} upcoming matches")

    # metrics of the settings the model was actually fit with (final_test_*); older runs only have test_*
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
