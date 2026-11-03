#!/bin/bash
# Runs once, on the first start of an empty data volume.
# POSTGRES_DB (service) is created by the image itself; this adds the tool databases.
set -euo pipefail

for db in "$AIRFLOW_DB" "$MLFLOW_DB"; do
  echo "creating database: $db"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE \"$db\" OWNER \"$POSTGRES_USER\";"
done
