"""경기 결과 예측 모델 DAG 두 개(ANL-03, ANL-09, ANL-10, 요구사항 2.6).

ml_weekly_retrain   매주 월요일 04:00   특징 → Hyperopt + Spark MLlib 학습 → MLflow `match-winner`
                                       (챔피언 설정과 새 설정을 같은 데이터로 겨뤄 이긴 쪽을 새 `champion`으로 등록)
ml_daily_predict    매일 07:00          특징 → `champion`이 다가오는 경기 예측 → match_prediction,
                                       끝난 경기는 is_correct 채점(경기 전 예측만 셈)
둘 다 먼저 특징을 다시 만든다. 그래서 kovo_daily(06:00)가 방금 갱신한 Elo와 결과를 쓴다.
"""

import pendulum
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args(retries=2)  # 실패 시 2번 재시도 + 실패 알림
START = pendulum.datetime(2026, 11, 1, tz="Asia/Seoul")


@dag(dag_id="ml_weekly_retrain", schedule="0 4 * * 1", start_date=START, catchup=False, max_active_runs=1,
     default_args=DEFAULT_ARGS, on_failure_callback=alerts.on_dag_failure, tags=["ml"], doc_md=__doc__)
def ml_weekly_retrain():
    @task
    def build_features():
        """경기별 특징을 다시 만든다(Spark)."""
        p.run_spark_job("build_features.py")

    @task
    def train():
        """Hyperopt 30번 시도로 모델을 학습·비교·등록한다(Spark)."""
        p.run_spark_job("train_model.py", "--evals", "30")

    build_features() >> train()


@dag(dag_id="ml_daily_predict", schedule="0 7 * * *", start_date=START, catchup=False, max_active_runs=1,
     default_args=DEFAULT_ARGS, on_failure_callback=alerts.on_dag_failure, tags=["ml"], doc_md=__doc__)
def ml_daily_predict():
    # kovo_daily / kovo_reprocess가 정제 중이면 기다린다(특징에 최신 결과가 들어가도록).
    # 3시간이 지나면 기다림은 실패(알림)하지만, 예측은 있는 데이터로 그대로 만든다(아래 trigger_rule="all_done")
    @task.sensor(poke_interval=120, timeout=3 * 3600, mode="reschedule", retries=0)
    def wait_for_curation() -> bool:
        """정제 DAG가 하나도 돌고 있지 않으면 True."""
        busy = p.curation_running()
        print("waiting for:", ", ".join(busy) if busy else "nothing")
        return not busy

    @task(trigger_rule="all_done")  # 앞 작업이 실패해도 실행한다
    def build_features():
        """경기별 특징을 다시 만든다(Spark)."""
        p.run_spark_job("build_features.py")

    @task
    def predict():
        """챔피언 모델로 예측하고 채점한다(Spark)."""
        p.run_spark_job("predict_matches.py")

    wait_for_curation() >> build_features() >> predict()


ml_weekly_retrain()
ml_daily_predict()
