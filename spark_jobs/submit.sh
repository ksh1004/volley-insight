#!/bin/sh
# Submit a PySpark job to YARN from the airflow container (client mode: the driver runs here).
#
#   docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh curate_kovo.py --seasons 022,023
#
# Same settings as the Airflow DAGs (airflow/dags). Executors run on the YARN NodeManager.
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
