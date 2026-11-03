"""Environment check: runs on YARN, computes a small result and writes it to HDFS.

Submitted by the `dev_smoke_spark_yarn` DAG (airflow/dags/dev_smoke.py).
"""
import random
import sys

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("dev-smoke-pi").getOrCreate()
n = 200_000
inside = spark.sparkContext.parallelize(range(n), 4).filter(
    lambda _: random.random() ** 2 + random.random() ** 2 < 1
).count()
pi = 4.0 * inside / n

out = "hdfs://namenode:9000/tmp/dev-smoke/pi"
spark.createDataFrame([(pi, sys.version.split()[0])], ["pi", "python"]).write.mode("overwrite").parquet(out)
print(f"pi ~= {pi:.4f}, written to {out}")
spark.stop()
