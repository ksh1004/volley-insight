"""Consumer lag of every Kafka consumer group -> Elasticsearch `metrics-volley` (Kibana dashboard, ADM-04).

Metricbeat's kafka module only reports groups that have live members. Flink's KafkaSource assigns partitions
itself and only *commits* offsets under its group id (raw-archiver, article-enricher), so those groups have no
members and Metricbeat never shows them. This loop reads the committed offset of every group and the log end
offset of each partition, and writes lag = end - committed with the same field names Metricbeat uses.

  python -m collectors kafka-lag [--every 30] [--once]     (compose service `kafka-lag`, search profile)
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
INDEX = "metrics-volley"  # data stream created by Metricbeat's template (metrics-volley*)


def lag_rows(admin, consumer):
    """[(group, topic, partition, committed, end, lag)] for every group with committed offsets."""
    from confluent_kafka import ConsumerGroupTopicPartitions

    rows = []
    for g in admin.list_consumer_groups(request_timeout=10).result().valid:
        res = admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(g.group_id)])[g.group_id].result()
        for tp in res.topic_partitions:
            if tp.offset < 0:  # nothing committed yet
                continue
            _, end = consumer.get_watermark_offsets(tp, timeout=10, cached=False)
            rows.append((g.group_id, tp.topic, tp.partition, tp.offset, end, max(0, end - tp.offset)))
    return rows


def to_docs(rows, now=None):
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")
    for group, topic, partition, committed, end, lag in rows:
        yield {"@timestamp": ts, "event": {"module": "kafka", "dataset": "kafka.consumergroup"},
               "metricset": {"name": "consumergroup"}, "agent": {"type": "kafka-lag"},
               "kafka": {"consumergroup": {"id": group, "consumer_lag": lag, "offset": committed},
                         "topic": {"name": topic}, "partition": {"id": partition, "offset": {"newest": end}}}}


def ship(docs, http=requests):
    body = "".join(json.dumps({"create": {}}) + "\n" + json.dumps(d) + "\n" for d in docs)
    if not body:
        return 0
    resp = http.post(f"{ES_URL}/{INDEX}/_bulk", data=body.encode(), timeout=30,
                     headers={"Content-Type": "application/x-ndjson"})
    resp.raise_for_status()
    if resp.json().get("errors"):
        raise RuntimeError(f"bulk errors: {resp.text[:300]}")
    return body.count("\n") // 2


def run(every=30.0, once=False):
    from confluent_kafka import Consumer
    from confluent_kafka.admin import AdminClient

    conf = {"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS}
    admin = AdminClient(conf)
    consumer = Consumer({**conf, "group.id": "kafka-lag-reader", "enable.auto.commit": False})
    try:
        while True:
            try:
                rows = lag_rows(admin, consumer)
                n = ship(to_docs(rows))
                log.info("lag: %s", ", ".join(f"{g}/{t}:{lag}" for g, t, _, _, _, lag in rows) or "no groups")
                log.debug("shipped %d documents", n)
            except Exception as exc:  # Kafka or Elasticsearch restarting: try again next round
                log.warning("lag check failed: %s", exc)
                if once:
                    raise
            if once:
                return
            time.sleep(every)
    finally:
        consumer.close()
