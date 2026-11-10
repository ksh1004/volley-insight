"""raw-archiver: 수집된 모든 Kafka 메시지를 그대로 HDFS에 복사해 보관한다(PIPE-03, 설계서 1.9).

  Kafka 토픽 kovo.*, news.*, ops.*  →  hdfs://namenode:9000/raw/topic={토픽}/dt={yyyy-MM-dd}/part-*
  (live.metrics는 Flink가 계산해 낸 결과물이라 보관하지 않는다.)

왜 필요한가: Kafka는 메시지를 7일만 보관한다. 원본을 HDFS에 영구 보관해 두면 Spark가 언제든 처음부터
다시 정제할 수 있다(ELT의 '원본 적재' 단계, 데이터 레이크의 raw 영역).

- 메시지 하나 = 한 줄: 수집기가 만든 JSON 봉투를 바이트 그대로 쓴다. Spark는 spark.read.json으로 읽는다.
- dt는 Kafka 레코드 시각(서울 기준) 날짜, 즉 메시지를 수집한 날이다.
- 파일은 128MB가 되거나 열린 지 5분이 지나면 마감한다(1분마다 확인. 관찰: 04:59:4x에 열린 경기 파일이
  05:05:34에 닫힘). Flink가 더 일찍 닫기도 해서 몇 MB짜리 파일도 정상이다. 쓰는 중인 파일은 숨김 파일
  (.part-*.inprogress)이고 다음 체크포인트(30초마다, compose.yaml) 때 보이게 되므로, 읽는 쪽은 반쪽짜리
  파일을 보지 않는다. `_SUCCESS` 파일은 그 파티션에 마감된 파일이 하나 이상 있다는 표시다.
- Kafka 오프셋은 체크포인트 때 `raw-archiver` 그룹으로 커밋된다. 작업을 재시작·재제출하면 그 위치부터 이어
  가고(전부 다시 보관하지 않음), 처음 보는 그룹이면 Kafka에 남아 있는 가장 오래된 메시지부터 시작한다(7일 보관).
  장애 뒤 드물게 생기는 중복은 Spark가 (키 + fetched_at)으로 없앤다.

실행(stream 프로필):
  docker compose exec flink-jobmanager flink run -d -py /opt/flink/usrlib/jobs/raw_archiver.py
  (평소에는 flink-job-keeper 서비스가 submit.sh로 자동 제출한다)
"""
import os

from pyflink.table import EnvironmentSettings, TableEnvironment

KAFKA = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
RAW_PATH = os.environ.get("RAW_PATH", "hdfs://namenode:9000/raw")
TOPIC_PATTERN = r"(kovo|news|ops)\..*"  # 보관할 토픽들(정규식)

# Flink Table API(SQL로 스트림 처리를 정의)를 스트리밍 모드로 연다
t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
config = t_env.get_config()
config.set("pipeline.name", "raw-archiver")  # Flink 화면과 submit.sh가 작업을 찾을 때 쓰는 이름
config.set("table.local-time-zone", "Asia/Seoul")  # dt 날짜를 서울 기준으로 자른다
# 파일 마감과 Kafka 오프셋 커밋은 체크포인트 때만 일어난다. 클러스터 설정에만 두면 안 되고 여기서 정해야 한다:
# 작업은 이 설정을 '제출한 쪽'(예: flink-job-keeper 컨테이너)에서 가져가기 때문이다.
config.set("execution.checkpointing.interval", "30 s")
# 이 작업 전용 체크포인트 폴더. 작업이 멈춰도 지우지 않는다: 재시작 때 submit.sh가 가장 최근 것에서 복원한다.
config.set("state.checkpoints.dir", "hdfs://namenode:9000/flink/checkpoints/raw-archiver")
config.set("execution.checkpointing.externalized-checkpoint-retention", "RETAIN_ON_CANCELLATION")

# 입력 테이블: Kafka 메시지 본문(line)을 문자열 그대로, 토픽 이름과 메시지 시각은 메타데이터에서 꺼낸다
t_env.execute_sql(f"""
    CREATE TABLE kafka_in (
        `line`  STRING,
        `topic` STRING METADATA VIRTUAL,
        `ts`    TIMESTAMP_LTZ(3) METADATA FROM 'timestamp' VIRTUAL
    ) WITH (
        'connector' = 'kafka',
        'topic-pattern' = '{TOPIC_PATTERN}',
        'properties.bootstrap.servers' = '{KAFKA}',
        'properties.group.id' = 'raw-archiver',
        'properties.auto.offset.reset' = 'earliest',
        'scan.startup.mode' = 'group-offsets',
        'scan.topic-partition-discovery.interval' = '60 s',
        'format' = 'raw'
    )
""")

# 출력 테이블: HDFS 파일. topic, dt 값에 따라 폴더(파티션)가 나뉜다
t_env.execute_sql(f"""
    CREATE TABLE hdfs_raw (
        `line`  STRING,
        `topic` STRING,
        `dt`    STRING
    ) PARTITIONED BY (`topic`, `dt`) WITH (
        'connector' = 'filesystem',
        'path' = '{RAW_PATH}',
        'format' = 'raw',
        'sink.rolling-policy.file-size' = '128MB',
        'sink.rolling-policy.rollover-interval' = '5 min',
        'sink.rolling-policy.check-interval' = '1 min',
        'sink.partition-commit.policy.kind' = 'success-file'
    )
""")

# 실제 작업: Kafka에서 읽은 줄을 그대로, 수집 날짜(dt) 폴더에 쓴다. 이 문장이 스트림 작업을 시작한다
t_env.execute_sql("""
    INSERT INTO hdfs_raw
    SELECT `line`, `topic`, DATE_FORMAT(`ts`, 'yyyy-MM-dd') FROM kafka_in
""")
