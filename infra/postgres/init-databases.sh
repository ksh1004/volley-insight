#!/bin/bash
# 데이터 볼륨이 비어 있는 첫 시작 때 한 번만 실행된다(PostgreSQL 이미지의 docker-entrypoint-initdb.d 규칙).
# POSTGRES_DB(서비스 DB)는 이미지가 직접 만들고, 이 스크립트는 도구용 DB(airflow, mlflow)를 더한다.
set -euo pipefail

for db in "$AIRFLOW_DB" "$MLFLOW_DB"; do
  echo "creating database: $db"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE \"$db\" OWNER \"$POSTGRES_USER\";"
done
