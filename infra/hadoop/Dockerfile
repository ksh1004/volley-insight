# One image for every Hadoop role (namenode, datanode, resourcemanager, nodemanager).
# Python 3.11 is included because PySpark executors run inside YARN containers
# and must use the same Python version as the Spark driver (Airflow image).
FROM eclipse-temurin:11-jre AS jre

FROM python:3.11-slim-bookworm
COPY --from=jre /opt/java/openjdk /opt/java/openjdk

ARG HADOOP_VERSION=3.4.1
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl procps \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL "https://archive.apache.org/dist/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}-lean.tar.gz" \
    | tar -xz -C /opt \
 && ln -s "/opt/hadoop-${HADOOP_VERSION}" /opt/hadoop \
 && rm -rf /opt/hadoop/share/doc

ENV JAVA_HOME=/opt/java/openjdk \
    HADOOP_HOME=/opt/hadoop \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    PATH=/opt/java/openjdk/bin:/opt/hadoop/bin:/opt/hadoop/sbin:$PATH \
    PYSPARK_PYTHON=/usr/local/bin/python3

COPY conf/ /opt/hadoop/etc/hadoop/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data/nn /data/dn /data/nm-local /data/nm-logs

ENTRYPOINT ["/entrypoint.sh"]
