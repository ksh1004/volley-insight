"""Development environment checks. Trigger manually from the Airflow UI."""
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
