"""모든 Kafka 컨슈머 그룹의 지연(lag)을 재서 Elasticsearch `metrics-volley`에 쓴다(Kibana 대시보드, ADM-04).

지연(lag) = 토픽에 쌓인 마지막 위치 - 컨슈머가 처리했다고 커밋한 위치. 클수록 처리가 밀려 있다는 뜻이다.

왜 따로 만들었나: Metricbeat의 kafka 모듈은 '살아 있는 멤버가 있는 그룹'만 보고한다. 그런데 Flink의
KafkaSource는 파티션을 직접 배정하고 자기 그룹 ID로 오프셋만 *커밋*한다(raw-archiver, article-enricher).
그래서 그 그룹에는 멤버가 없고 Metricbeat에 나타나지 않는다. 이 루프는 모든 그룹의 커밋 위치와 각 파티션의
끝 위치를 읽어, Metricbeat와 같은 필드 이름으로 lag = 끝 - 커밋 을 기록한다(대시보드를 그대로 쓰기 위해).

  python -m collectors kafka-lag [--every 30] [--once]     (compose 서비스 `kafka-lag`, search 프로필)
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from . import settings

log = logging.getLogger(__name__)
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200").rstrip("/")
INDEX = "metrics-volley"  # Metricbeat 템플릿(metrics-volley*)이 만든 데이터 스트림


def lag_rows(admin, consumer):
    """커밋된 오프셋이 있는 모든 그룹의 [(그룹, 토픽, 파티션, 커밋 위치, 끝 위치, 지연)] 목록."""
    from confluent_kafka import ConsumerGroupTopicPartitions

    rows = []
    for g in admin.list_consumer_groups(request_timeout=10).result().valid:
        res = admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(g.group_id)])[g.group_id].result()
        for tp in res.topic_partitions:
            if tp.offset < 0:  # 아직 아무것도 커밋하지 않음
                continue
            _, end = consumer.get_watermark_offsets(tp, timeout=10, cached=False)  # (처음 위치, 끝 위치)
            rows.append((g.group_id, tp.topic, tp.partition, tp.offset, end, max(0, end - tp.offset)))
    return rows


def to_docs(rows, now=None):
    """지연 행들을 Metricbeat의 kafka.consumergroup 문서와 같은 모양으로 바꾼다."""
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")
    for group, topic, partition, committed, end, lag in rows:
        yield {"@timestamp": ts, "event": {"module": "kafka", "dataset": "kafka.consumergroup"},
               "metricset": {"name": "consumergroup"}, "agent": {"type": "kafka-lag"},
               "kafka": {"consumergroup": {"id": group, "consumer_lag": lag, "offset": committed},
                         "topic": {"name": topic}, "partition": {"id": partition, "offset": {"newest": end}}}}


def ship(docs, http=requests):
    """문서들을 Elasticsearch _bulk API로 한 번에 넣고 넣은 개수를 돌려준다.
    데이터 스트림에는 "create" 동작만 쓸 수 있어 문서마다 {"create": {}} 줄을 앞에 붙인다(NDJSON 형식)."""
    body = "".join(json.dumps({"create": {}}) + "\n" + json.dumps(d) + "\n" for d in docs)
    if not body:
        return 0
    resp = http.post(f"{ES_URL}/{INDEX}/_bulk", data=body.encode(), timeout=30,
                     headers={"Content-Type": "application/x-ndjson"})
    resp.raise_for_status()
    if resp.json().get("errors"):  # _bulk는 일부 실패해도 200을 주므로 본문의 errors를 따로 확인한다
        raise RuntimeError(f"bulk errors: {resp.text[:300]}")
    return body.count("\n") // 2


def run(every=30.0, once=False):
    """every초마다 지연을 재서 기록한다. once=True면 한 번만 하고 끝낸다(실패하면 예외)."""
    from confluent_kafka import Consumer
    from confluent_kafka.admin import AdminClient

    conf = {"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS}
    admin = AdminClient(conf)
    # 끝 위치를 읽기 위한 컨슈머. 아무것도 커밋하지 않는다(자기 지연이 대시보드에 섞이지 않게)
    consumer = Consumer({**conf, "group.id": "kafka-lag-reader", "enable.auto.commit": False})
    try:
        while True:
            try:
                rows = lag_rows(admin, consumer)
                n = ship(to_docs(rows))
                log.info("lag: %s", ", ".join(f"{g}/{t}:{lag}" for g, t, _, _, _, lag in rows) or "no groups")
                log.debug("shipped %d documents", n)
            except Exception as exc:  # Kafka나 Elasticsearch가 재시작 중: 다음 회차에 다시 시도
                log.warning("lag check failed: %s", exc)
                if once:
                    raise
            if once:
                return
            time.sleep(every)
    finally:
        consumer.close()
