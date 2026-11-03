"""raw-archiver: copy every collected Kafka message to HDFS as-is (PIPE-03, design.md 1.9).

  Kafka topics kovo.*, news.*, ops.*  ->  hdfs://namenode:9000/raw/topic={topic}/dt={yyyy-MM-dd}/part-*
  (live.metrics is derived output, so it is not archived.)

- One line per message: the collector's JSON envelope, byte for byte. Spark reads it with spark.read.json.
- dt is the Kafka record time in Asia/Seoul, i.e. the day the message was collected.
- A file is finished at 128 MB or once it has been open for 5 minutes (checked every minute; observed: a
  match file opened 04:59:4x closed 05:05:34). Flink sometimes closes files earlier, so files of a few MB are
  normal. Files still being written are hidden (.part-*.inprogress) and become visible on the next
  checkpoint (every 30 s, compose.yaml), so readers never see half a file. A `_SUCCESS` file marks a
  partition that has received at least one finished file.
- Kafka offsets are committed on checkpoint under group `raw-archiver`. A restarted or resubmitted job
  continues from there instead of archiving everything again; a brand-new group starts from the earliest
  message still in Kafka (7-day retention). Rare duplicates after a crash are removed by Spark (key + fetched_at).

Run (stream profile):
  docker compose exec flink-jobmanager flink run -d -py /opt/flink/usrlib/jobs/raw_archiver.py
"""
import os

from pyflink.table import EnvironmentSettings, TableEnvironment

KAFKA = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
RAW_PATH = os.environ.get("RAW_PATH", "hdfs://namenode:9000/raw")
TOPIC_PATTERN = r"(kovo|news|ops)\..*"

t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
config = t_env.get_config()
config.set("pipeline.name", "raw-archiver")
config.set("table.local-time-zone", "Asia/Seoul")

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

t_env.execute_sql("""
    INSERT INTO hdfs_raw
    SELECT `line`, `topic`, DATE_FORMAT(`ts`, 'yyyy-MM-dd') FROM kafka_in
""")
