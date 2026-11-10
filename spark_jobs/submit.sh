#!/bin/sh
# PySpark 작업을 airflow 컨테이너에서 YARN으로 제출한다(client 모드: 드라이버는 이 컨테이너에서, 실제 계산을
# 하는 executor는 YARN NodeManager 컨테이너에서 돈다).
#
#   docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh curate_kovo.py --seasons 022,023
#   (인자 1: spark_jobs 안의 작업 파일, 나머지: 그 작업에 넘길 인자)
#
# Airflow DAG(airflow/dags, kovo_tasks/pipeline.py의 SPARK_CONF)와 같은 설정이라, 손으로 돌려도 결과가 같다.
# 메모리 값은 이 PC(Docker 메모리 한도) 기준이다: 드라이버 1GB, executor 1개 × 2코어 × 1.5GB.
set -eu
job="$1"; shift
exec spark-submit \
  --master yarn --deploy-mode client \
  --name "$(basename "$job" .py)" \
  --jars /opt/spark-jars/postgresql.jar,/opt/spark-jars/elasticsearch-spark.jar \
  --conf spark.driver.host=airflow \
  --conf spark.driver.bindAddress=0.0.0.0 \
  --conf spark.driver.memory=1g \
  --conf spark.executor.instances=1 \
  --conf spark.executor.cores=2 \
  --conf spark.executor.memory=1536m \
  --conf spark.yarn.am.memory=512m \
  --conf spark.sql.shuffle.partitions=8 \
  --conf spark.executorEnv.PYSPARK_PYTHON=/usr/local/bin/python3 \
  --conf spark.yarn.appMasterEnv.PYSPARK_PYTHON=/usr/local/bin/python3 \
  "/opt/airflow/spark_jobs/$job" "$@"
