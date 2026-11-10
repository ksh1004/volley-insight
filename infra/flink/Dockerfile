# Flink 1.20 이미지에 PyFlink(파이썬 작업), Kafka/JDBC 커넥터, 하둡 클라이언트(HDFS에 쓰기용)를 더한다.
# 하둡 파일은 hadoop 서비스 이미지에서 복사한다(compose.yaml의 additional_contexts 참고).
FROM flink:1.20.1-java17

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-dev \
 && ln -sf /usr/bin/python3 /usr/bin/python \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir "apache-flink==1.20.1" "elasticsearch==8.17.2" "psycopg2-binary==2.9.10"

# 커넥터 JAR들을 Flink lib 폴더에 받아 둔다(Kafka SQL 커넥터, JDBC 커넥터, PostgreSQL 드라이버)
ARG MAVEN=https://repo1.maven.org/maven2
RUN cd /opt/flink/lib \
 && curl -fsSLO "$MAVEN/org/apache/flink/flink-sql-connector-kafka/3.3.0-1.20/flink-sql-connector-kafka-3.3.0-1.20.jar" \
 && curl -fsSLO "$MAVEN/org/apache/flink/flink-connector-jdbc/3.3.0-1.20/flink-connector-jdbc-3.3.0-1.20.jar" \
 && curl -fsSLO "$MAVEN/org/postgresql/postgresql/42.7.5/postgresql-42.7.5.jar"

# HDFS에 쓰려면 하둡 클래스들이 필요하다(HADOOP_CLASSPATH로 Flink에 알려 줌)
COPY --from=hadoop /opt/hadoop /opt/hadoop
ENV HADOOP_HOME=/opt/hadoop \
    HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop \
    HADOOP_CLASSPATH=/opt/hadoop/etc/hadoop:/opt/hadoop/share/hadoop/common/lib/*:/opt/hadoop/share/hadoop/common/*:/opt/hadoop/share/hadoop/hdfs:/opt/hadoop/share/hadoop/hdfs/lib/*:/opt/hadoop/share/hadoop/hdfs/*:/opt/hadoop/share/hadoop/yarn/lib/*:/opt/hadoop/share/hadoop/yarn/*:/opt/hadoop/share/hadoop/mapreduce/*
