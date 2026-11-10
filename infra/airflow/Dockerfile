# Airflow 3 이미지(`airflow standalone`으로 LocalExecutor 실행). Spark 드라이버 역할도 한다:
# Java 11 + PySpark로 YARN에 작업을 제출한다(client 모드). 하둡 설정은 /opt/hadoop-conf(compose가 연결).
# 수집기 의존성도 들어 있어 live-collector, kafka-lag 서비스가 이 이미지를 그대로 쓴다.
# Java 11 실행 환경만 가져오기 위한 단계(멀티 스테이지 빌드)
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
# ML 라이브러리는 따로 설치한다(이유는 requirements-ml.txt 참고)
RUN pip install --no-cache-dir -r /requirements-ml.txt

# spark-submit --jars로 넘길 JAR: PostgreSQL JDBC 드라이버(서비스 DB 적재), elasticsearch-hadoop(index_entities.py)
USER root
RUN mkdir -p /opt/spark-jars \
 && curl -fsSL -o /opt/spark-jars/postgresql.jar \
      https://repo1.maven.org/maven2/org/postgresql/postgresql/42.7.5/postgresql-42.7.5.jar  && curl -fsSL -o /opt/spark-jars/elasticsearch-spark.jar       https://repo1.maven.org/maven2/org/elasticsearch/elasticsearch-spark-30_2.12/8.17.4/elasticsearch-spark-30_2.12-8.17.4.jar
USER airflow
