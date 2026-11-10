"""개발 환경 점검용 Spark 작업: YARN에서 돌아 작은 결과(원주율 근사값)를 계산하고 HDFS에 쓴다.

`dev_smoke_spark_yarn` DAG(airflow/dags/dev_smoke.py)가 제출한다. Airflow → Spark(YARN) → HDFS 연결이
모두 살아 있는지 한 번에 확인하는 용도다.
"""
import random
import sys

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("dev-smoke-pi").getOrCreate()
# 몬테카를로 방법: 정사각형 안의 무작위 점 중 사분원 안에 들어간 비율 × 4 ≈ 원주율
n = 200_000
inside = spark.sparkContext.parallelize(range(n), 4).filter(
    lambda _: random.random() ** 2 + random.random() ** 2 < 1
).count()
pi = 4.0 * inside / n

out = "hdfs://namenode:9000/tmp/dev-smoke/pi"
# 결과와 실행한 파이썬 버전을 함께 남긴다(executor의 파이썬 버전 확인용)
spark.createDataFrame([(pi, sys.version.split()[0])], ["pi", "python"]).write.mode("overwrite").parquet(out)
print(f"pi ~= {pi:.4f}, written to {out}")
spark.stop()
