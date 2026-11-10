"""Spark DataFrame들을 PostgreSQL에 적재하고, 한 트랜잭션으로 서비스 테이블에 병합한다(드라이버에서 실행, client 모드).

모든 Spark 작업이 이 함수 하나로 DB에 쓴다(curate_kovo, build_marts, player_types, player_ratings, train_model,
predict_matches). 순서는 이렇다.
  1. 실행마다 전용 staging 스키마(staging_<애플리케이션 id>)를 만든다 → 동시에 도는 작업끼리 덮어쓰지 않는다.
  2. DataFrame들을 그 스키마의 테이블로 JDBC 적재한다(병렬, 5000행씩 묶어서).
  3. SQL 파일의 `staging.`을 이번 스키마 이름으로 바꿔, 하나의 트랜잭션에서 실행한다(advisory lock으로 같은
     종류의 병합은 한 번에 하나만). 트랜잭션이라 중간에 실패하면 전부 취소되고, 사이트는 반쯤 바뀐 데이터를 보지 않는다.
  4. 끝나면(실패해도) staging 스키마를 지운다.
"""
import os

import psycopg2


def load_and_merge(spark, tables, sql_path, lock_name):
    """tables({테이블 이름: DataFrame})를 staging에 넣고 sql_path의 병합 SQL을 실행한다.
    lock_name: 잠금 이름(같은 이름의 병합끼리 순서대로 실행됨). SQL의 마지막 SELECT 결과(요약)를 돌려준다."""
    host, port = os.environ.get("POSTGRES_HOST", "postgres"), os.environ.get("POSTGRES_PORT", "5432")
    db, user, password = os.environ.get("SERVICE_DB", "service"), os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"]
    connect = lambda: psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)  # noqa: E731
    # 애플리케이션 id(예: application_123_0004)를 스키마 이름으로 쓸 수 있게 영문·숫자 외 문자를 _로 바꾼다
    schema = "staging_" + "".join(ch if ch.isalnum() else "_" for ch in spark.sparkContext.applicationId.lower())
    admin = connect()  # 스키마 생성·삭제용 연결(트랜잭션 없이 바로 적용)
    admin.autocommit = True
    admin.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    try:
        # rewriteBatchedInserts: 여러 INSERT를 한 문장으로 묶어 보내 적재가 훨씬 빨라진다
        url = f"jdbc:postgresql://{host}:{port}/{db}?rewriteBatchedInserts=true"
        for name, df in tables.items():
            df.write.format("jdbc").mode("overwrite").options(
                url=url, dbtable=f"{schema}.{name}", user=user, password=password,
                driver="org.postgresql.Driver", batchsize="5000").save()
        with open(sql_path, encoding="utf-8") as f:
            sql = f.read().replace("staging.", f"{schema}.")
        with connect() as tx, tx.cursor() as cur:  # 하나의 트랜잭션(블록이 끝나면 커밋, 예외면 롤백)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_name,))  # 트랜잭션이 끝나면 자동 해제
            cur.execute(sql)
            return cur.fetchall() if cur.description else []
    finally:
        admin.cursor().execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()
