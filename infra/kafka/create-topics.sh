#!/bin/bash
# 설계서 1.7의 Kafka 토픽들을 만든다(kafka-init 서비스가 실행). 이미 있으면 건너뛰므로 여러 번 돌려도 안전하다.
# 모든 토픽: 복제 1(브로커 하나), lz4 압축, 7일 보관. 숫자는 파티션 수(동시에 나눠 처리할 수 있는 단위).
set -euo pipefail
BS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
KT=/opt/kafka/bin/kafka-topics.sh

# Kafka가 응답할 때까지 기다린다
until $KT --bootstrap-server "$BS" --list >/dev/null 2>&1; do
  echo "waiting for kafka at $BS ..."; sleep 2
done

create() { # 인자: 토픽 이름, 파티션 수
  $KT --bootstrap-server "$BS" --create --if-not-exists --topic "$1" --partitions "$2" \
    --replication-factor 1 --config compression.type=lz4 --config retention.ms=604800000
}

create kovo.schedule 1
create kovo.match 3
create kovo.rally 3
create kovo.live 3
create kovo.player 1
create news.article 1
create live.metrics 3
create ops.collection-log 1

# 만든 결과 확인용 목록 출력
$KT --bootstrap-server "$BS" --list
