"""PyFlink와 Kafka 커넥터가 제대로 설치됐는지 확인하는 점검용 작업(개발 환경 점검).

`kovo.live` 토픽을 읽어 레코드마다 출력한다(TaskManager 로그에 찍힘). jobmanager 컨테이너에서 실행:
  docker compose exec flink-jobmanager flink run -d -py /opt/flink/usrlib/jobs/smoke_job.py
확인이 끝나면 Flink 화면에서 작업을 취소한다.
"""
from pyflink.table import EnvironmentSettings, TableEnvironment

t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
# 입력: kovo.live 메시지 본문을 문자열 그대로(처음 메시지부터)
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
# 출력: print 커넥터는 받은 행을 로그에 찍는다
t_env.execute_sql("CREATE TABLE out_print (payload STRING) WITH ('connector' = 'print')")
t_env.execute_sql("INSERT INTO out_print SELECT payload FROM live_events")
