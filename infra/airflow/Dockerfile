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
COPY --chown=airflow:root requirements.txt /requirements.txt
USER airflow
RUN pip install --no-cache-dir "apache-airflow==3.0.6" -r /requirements.txt
