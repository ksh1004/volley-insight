"""개발 환경 점검용 DAG. Airflow 화면에서 손으로 실행한다(일정 없음).

Airflow → Spark(YARN) → HDFS로 이어지는 연결이 살아 있는지 spark_jobs/smoke_pi.py 하나로 확인한다.
"""
from datetime import datetime

from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.sdk import DAG

with DAG(
    dag_id="dev_smoke_spark_yarn",
    description="Spark on YARN -> HDFS write check (requirements 2.8: Spark runs on YARN)",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["dev"],
):
    # 점검용이라 메모리를 작게 잡는다. driver.host는 YARN이 airflow 컨테이너의 드라이버로 다시 접속할 주소
    SparkSubmitOperator(
        task_id="spark_pi_on_yarn",
        conn_id="spark_yarn",
        application="/opt/airflow/spark_jobs/smoke_pi.py",
        name="dev-smoke-pi",
        conf={
            "spark.driver.host": "airflow",
            "spark.driver.bindAddress": "0.0.0.0",
            "spark.executor.instances": "1",
            "spark.executor.memory": "512m",
            "spark.yarn.am.memory": "512m",
            "spark.executorEnv.PYSPARK_PYTHON": "/usr/local/bin/python3",
            "spark.yarn.appMasterEnv.PYSPARK_PYTHON": "/usr/local/bin/python3",
        },
    )
