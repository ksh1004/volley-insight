#!/bin/bash
# Creates the topics from design.md 1.7. Safe to run again (--if-not-exists).
set -euo pipefail
BS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
KT=/opt/kafka/bin/kafka-topics.sh

until $KT --bootstrap-server "$BS" --list >/dev/null 2>&1; do
  echo "waiting for kafka at $BS ..."; sleep 2
done

create() { # name partitions
  $KT --bootstrap-server "$BS" --create --if-not-exists --topic "$1" --partitions "$2" \
    --replication-factor 1 --config compression.type=lz4 --config retention.ms=604800000
}

create kovo.schedule 1
create kovo.match 3
create kovo.rally 3
create kovo.live 3
create news.article 1
create live.metrics 3
create ops.collection-log 1

$KT --bootstrap-server "$BS" --list
