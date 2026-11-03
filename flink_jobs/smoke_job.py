"""Environment check for PyFlink + Kafka connector.

Reads `kovo.live` and prints each record. Run inside the jobmanager container:
  docker compose exec flink-jobmanager flink run -d -py /opt/flink/usrlib/jobs/smoke_job.py
"""
from pyflink.table import EnvironmentSettings, TableEnvironment

t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
t_env.execute_sql("""
    CREATE TABLE live_events (
        payload STRING
    ) WITH (
        'connector' = 'kafka',
        'topic' = 'kovo.live',
        'properties.bootstrap.servers' = 'kafka:9092',
        'properties.group.id' = 'dev-smoke',
        'scan.startup.mode' = 'earliest-offset',
        'format' = 'raw'
    )
""")
t_env.execute_sql("CREATE TABLE out_print (payload STRING) WITH ('connector' = 'print')")
t_env.execute_sql("INSERT INTO out_print SELECT payload FROM live_events")
