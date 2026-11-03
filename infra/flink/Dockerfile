# Flink 1.20 with PyFlink, Kafka/JDBC connectors and the Hadoop client (for HDFS sinks).
# The Hadoop files come from the hadoop service image (see additional_contexts in docker-compose.yml).
FROM flink:1.20.1-java17

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-dev \
 && ln -sf /usr/bin/python3 /usr/bin/python \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir "apache-flink==1.20.1" "elasticsearch==8.17.2" "psycopg2-binary==2.9.10"

ARG MAVEN=https://repo1.maven.org/maven2
RUN cd /opt/flink/lib \
 && curl -fsSLO "$MAVEN/org/apache/flink/flink-sql-connector-kafka/3.3.0-1.20/flink-sql-connector-kafka-3.3.0-1.20.jar" \
 && curl -fsSLO "$MAVEN/org/apache/flink/flink-connector-jdbc/3.3.0-1.20/flink-connector-jdbc-3.3.0-1.20.jar" \
 && curl -fsSLO "$MAVEN/org/postgresql/postgresql/42.7.5/postgresql-42.7.5.jar"

COPY --from=hadoop /opt/hadoop /opt/hadoop
ENV HADOOP_HOME=/opt/hadoop \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    HADOOP_CLASSPATH=/opt/hadoop/etc/hadoop:/opt/hadoop/share/hadoop/common/lib/*:/opt/hadoop/share/hadoop/common/*:/opt/hadoop/share/hadoop/hdfs:/opt/hadoop/share/hadoop/hdfs/lib/*:/opt/hadoop/share/hadoop/hdfs/*:/opt/hadoop/share/hadoop/yarn/lib/*:/opt/hadoop/share/hadoop/yarn/*:/opt/hadoop/share/hadoop/mapreduce/*
