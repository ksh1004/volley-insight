# Airflow 3 (LocalExecutor via `airflow standalone`) that can also act as the Spark driver:
# Java 11 + PySpark submit jobs to YARN (client mode), using the Hadoop configs in /opt/hadoop-conf.
FROM eclipse-temurin:11-jre AS jre

FROM apache/airflow:3.0.6-python3.11
USER root
COPY --from=jre /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    HADOOP_CONF_DIR=/opt/hadoop-conf \
    YARN_CONF_DIR=/opt/hadoop-conf \
    PYSPARK_PYTHON=/usr/local/bin/python3
COPY --chown=airflow:root requirements.txt requirements-ml.txt /
USER airflow
RUN pip install --no-cache-dir "apache-airflow==3.0.6" -r /requirements.txt
# ML client libraries in a separate step (see requirements-ml.txt)
RUN pip install --no-cache-dir -r /requirements-ml.txt

# PostgreSQL JDBC driver (serving DB) and elasticsearch-hadoop (index_entities.py) for spark-submit --jars.
USER root
RUN mkdir -p /opt/spark-jars \
 && curl -fsSL -o /opt/spark-jars/postgresql.jar \
      https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.5/postgresql-42.7.5.jar  && curl -fsSL -o /opt/spark-jars/elasticsearch-spark.jar       https://repo1.maven.org/maven2/org/elasticsearch/elasticsearch-spark-30_2.12/8.17.4/elasticsearch-spark-30_2.12-8.17.4.jar
USER airflow
