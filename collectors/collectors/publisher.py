"""Kafka producer with the settings from requirements 2.5 (acks=all, idempotence, lz4)."""
import json
import logging

from . import settings

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


class KafkaPublisher:
    def __init__(self, bootstrap_servers=None, producer=None):
        if producer is None:
            from confluent_kafka import Producer  # imported lazily so tests run without a broker

            producer = Producer({
                "bootstrap.servers": bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": 50,
            })
        self._producer = producer
        self._errors = []
        self.sent = 0

    def check(self, timeout: float = 10.0) -> None:
        """Fail fast when Kafka is unreachable, before any request is sent to KOVO."""
        try:
            self._producer.list_topics(timeout=timeout)
        except Exception as exc:
            raise PublishError(f"Kafka is not reachable ({exc}). Is `docker compose up -d` running?") from exc

    def publish(self, topic: str, key: str, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._producer.produce(topic, key=str(key).encode("utf-8"), value=data, on_delivery=self._on_delivery)
        self._producer.poll(0)
        self.sent += 1

    def _on_delivery(self, err, msg):
        if err is not None:
            self._errors.append(str(err))

    def flush(self, timeout: float = 30.0) -> None:
        """Wait for every message to be acknowledged; raise if any failed."""
        remaining = self._producer.flush(timeout)
        if remaining:
            raise PublishError(f"{remaining} messages were not delivered within {timeout}s")
        if self._errors:
            errors, self._errors = self._errors, []
            raise PublishError(f"{len(errors)} messages failed: {errors[:3]}")


class DryRunPublisher:
    """Prints instead of sending. Used by `--dry-run` to check a collector without Kafka."""

    def __init__(self):
        self.sent = 0
        self.messages = []

    def check(self, timeout: float = 10.0):
        pass

    def publish(self, topic, key, value):
        self.sent += 1
        self.messages.append((topic, key, value))
        log.info("[dry-run] %s key=%s endpoint=%s", topic, key, value.get("endpoint"))

    def flush(self, timeout: float = 30.0):
        pass
