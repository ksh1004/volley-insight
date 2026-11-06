"""Load Spark DataFrames into PostgreSQL and merge them in one transaction (driver side, client mode).

Each run writes to its own staging schema (staging_<application id>) so concurrent jobs never overwrite each
other, takes an advisory lock for the merge, and drops the schema afterwards. `staging.` in the SQL file is
replaced with the run's schema.
"""
import os

import psycopg2


def load_and_merge(spark, tables, sql_path, lock_name):
    host, port = os.environ.get("POSTGRES_HOST", "postgres"), os.environ.get("POSTGRES_PORT", "5432")
    db, user, password = os.environ.get("SERVICE_DB", "service"), os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"]
    connect = lambda: psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)  # noqa: E731
    schema = "staging_" + "".join(ch if ch.isalnum() else "_" for ch in spark.sparkContext.applicationId.lower())
    admin = connect()
    admin.autocommit = True
    admin.cursor().execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    try:
        url = f"jdbc:postgresql://{host}:{port}/{db}?rewriteBatchedInserts=true"
        for name, df in tables.items():
            df.write.format("jdbc").mode("overwrite").options(
                url=url, dbtable=f"{schema}.{name}", user=user, password=password,
                driver="org.postgresql.Driver", batchsize="5000").save()
        with open(sql_path, encoding="utf-8") as f:
            sql = f.read().replace("staging.", f"{schema}.")
        with connect() as tx, tx.cursor() as cur:  # one transaction
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_name,))
            cur.execute(sql)
            return cur.fetchall() if cur.description else []
    finally:
        admin.cursor().execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        admin.close()
