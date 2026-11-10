"""Kafka로 메시지를 보내는 발행기(producer). 요구사항 2.5의 설정을 따른다.

  acks=all            모든 복제본이 받아야 성공으로 친다(유실 방지)
  enable.idempotence  재전송돼도 같은 메시지가 두 번 저장되지 않게 한다(중복 방지)
  compression lz4     네트워크·디스크 사용량을 줄인다
"""
import json
import logging

from . import settings

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    """Kafka에 접속할 수 없거나 메시지 전달이 실패했을 때 던지는 예외."""


class KafkaPublisher:
    """실제 Kafka에 메시지를 보낸다. 보낸 뒤 반드시 flush()로 전달 완료를 확인해야 한다."""

    def __init__(self, bootstrap_servers=None, producer=None):
        """producer를 넘기면 그것을 쓴다(테스트용 가짜 producer). 없으면 confluent_kafka Producer를 만든다."""
        if producer is None:
            from confluent_kafka import Producer  # 테스트가 Kafka 없이도 돌도록 필요할 때만 import

            producer = Producer({
                "bootstrap.servers": bootstrap_servers or settings.KAFKA_BOOTSTRAP_SERVERS,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": 50,  # 50ms 동안 모아서 한 번에 보내 처리량을 높인다
            })
        self._producer = producer
        self._errors = []  # 전달 실패 콜백이 모아 두는 오류 목록(flush 때 한꺼번에 보고)
        self.sent = 0  # 보낸 메시지 수

    def check(self, timeout: float = 10.0) -> None:
        """Kafka에 접속되는지 먼저 확인한다. KOVO에 요청하기 전에 불러, 받아 놓고 못 보내는 일을 막는다."""
        try:
            self._producer.list_topics(timeout=timeout)
        except Exception as exc:
            raise PublishError(f"Kafka is not reachable ({exc}). Is `docker compose up -d` running?") from exc

    def publish(self, topic: str, key: str, value: dict) -> None:
        """value를 JSON(UTF-8, 공백 없음)으로 바꿔 보낸다. 실제 전송은 비동기라 결과는 flush()에서 확인한다."""
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._producer.produce(topic, key=str(key).encode("utf-8"), value=data, on_delivery=self._on_delivery)
        self._producer.poll(0)  # 그동안 도착한 전달 결과 콜백을 처리한다(기다리지 않음)
        self.sent += 1

    def _on_delivery(self, err, msg):
        """메시지 하나의 전달 결과 콜백. 실패만 모아 둔다."""
        if err is not None:
            self._errors.append(str(err))

    def flush(self, timeout: float = 30.0) -> None:
        """보낸 모든 메시지가 확인될 때까지 기다린다. 하나라도 실패하거나 시간 안에 못 끝나면 PublishError."""
        remaining = self._producer.flush(timeout)
        if remaining:
            raise PublishError(f"{remaining} messages were not delivered within {timeout}s")
        if self._errors:
            errors, self._errors = self._errors, []
            raise PublishError(f"{len(errors)} messages failed: {errors[:3]}")


class DryRunPublisher:
    """보내지 않고 로그만 찍는 발행기. `--dry-run` 옵션으로 Kafka 없이 수집기를 시험할 때 쓴다."""

    def __init__(self):
        self.sent = 0
        self.messages = []  # 보냈을 메시지 (topic, key, value) 목록

    def check(self, timeout: float = 10.0):
        pass

    def publish(self, topic, key, value):
        self.sent += 1
        self.messages.append((topic, key, value))
        log.info("[dry-run] %s key=%s endpoint=%s", topic, key, value.get("endpoint"))

    def flush(self, timeout: float = 30.0):
        pass
