# 하둡의 모든 역할(namenode, datanode, resourcemanager, nodemanager)이 함께 쓰는 이미지 하나.
# 역할은 실행할 때 인자로 정한다(entrypoint.sh).
# Python 3.11을 넣은 이유: PySpark executor가 YARN 컨테이너 안에서 돌고, Spark 드라이버(Airflow 이미지)와 같은
# 파이썬 버전을 써야 하기 때문이다.
FROM eclipse-temurin:11-jre AS jre

FROM python:3.11-slim-bookworm
COPY --from=jre /opt/java/openjdk /opt/java/openjdk

# 하둡 배포판(lean: 일부 클라우드 라이브러리를 뺀 가벼운 버전)을 받아 /opt/hadoop에 둔다
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

# 설정 파일(conf/*.xml)과 시작 스크립트, 데이터 폴더
COPY conf/ /opt/hadoop/etc/hadoop/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data/nn /data/dn /data/nm-local /data/nm-logs

ENTRYPOINT ["/entrypoint.sh"]
