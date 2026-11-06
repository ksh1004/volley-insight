"""Match outcome model (ANL-03, ANL-09, ANL-10; requirements 2.6).

ml_weekly_retrain   Monday 04:00   features -> Hyperopt + Spark MLlib training -> MLflow `match-winner`
                                   (new version: `champion` if better on the test season, else `candidate`)
ml_daily_predict    daily 07:00    features -> `champion` predicts upcoming matches -> match_prediction;
                                   finished matches get is_correct (pre-match predictions only)
Both rebuild the features first, so they use the Elo and results kovo_daily (06:00) has just refreshed.
"""

import pendulum
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args(retries=2)
START = pendulum.datetime(2026, 11, 1, tz="Asia/Seoul")


@dag(dag_id="ml_weekly_retrain", schedule="0 4 * * 1", start_date=START, catchup=False, max_active_runs=1,
     default_args=DEFAULT_ARGS, on_failure_callback=alerts.on_dag_failure, tags=["ml"], doc_md=__doc__)
def ml_weekly_retrain():
    @task
    def build_features():
        p.run_spark_job("build_features.py")

    @task
    def train():
        p.run_spark_job("train_model.py", "--evals", "30")

    build_features() >> train()


@dag(dag_id="ml_daily_predict", schedule="0 7 * * *", start_date=START, catchup=False, max_active_runs=1,
     default_args=DEFAULT_ARGS, on_failure_callback=alerts.on_dag_failure, tags=["ml"], doc_md=__doc__)
def ml_daily_predict():
    @task
    def build_features():
        p.run_spark_job("build_features.py")

    @task
    def predict():
        p.run_spark_job("predict_matches.py")

    build_features() >> predict()


ml_weekly_retrain()
ml_daily_predict()
