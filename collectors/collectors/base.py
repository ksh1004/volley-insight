"""모든 수집기가 공유하는 실행 흐름(설계서 4.7 BaseCollector).

하위 클래스는 세 가지만 정하면 된다.
  - collect(): 무엇을 가져올지 (API를 호출해 응답을 하나씩 내보냄)
  - topic: 어느 Kafka 토픽으로 보낼지
  - key_of(): 메시지 키를 무엇으로 할지 (같은 키는 같은 파티션으로 가서 순서가 보장됨)
나머지(실행 ID 발급, 수집 로그 기록, Kafka 발행, flush, 실패 보고)는 이 기반 클래스가 처리한다.
"""
import logging
import uuid

from .collection_log import CollectionLogger
from .envelope import make_envelope

log = logging.getLogger(__name__)


class BaseCollector:
    """수집기의 공통 뼈대. 하위 클래스가 name, topic, collect(), key_of()를 채운다."""

    name = "base"  # 수집 로그에 남는 작업 이름
    topic = ""  # 메시지를 보낼 Kafka 토픽
    source = "kovo"  # 봉투의 `source` 값(데이터 출처)

    def __init__(self, client, publisher):
        """client: API 호출 객체(예: KovoClient), publisher: Kafka 발행 객체(KafkaPublisher 또는 테스트용 가짜)."""
        self.client = client
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)
        self.run_id = None  # 가장 최근 실행의 ID. 그 실행에서 보낸 모든 봉투에 같은 ID가 붙는다

    # ---- 하위 클래스가 구현하는 부분
    def collect(self, **params):
        """(endpoint, 요청 파라미터, 응답 본문) 튜플을 하나씩 yield 한다."""
        raise NotImplementedError

    def key_of(self, endpoint: str, request_params: dict, body) -> str:
        """메시지의 Kafka 키를 돌려준다(예: 경기 키 "023-6")."""
        raise NotImplementedError

    def describe(self, **params) -> str:
        """수집 로그의 `target` 칸에 적을 사람이 읽는 설명. 기본은 "키=값" 나열."""
        return " ".join(f"{k}={v}" for k, v in params.items())

    def run(self, **params) -> int:
        """수집을 한 번 실행하고 보낸 메시지 수를 돌려준다. 실패하면 로그를 FAILED로 남기고 예외를 다시 던진다."""
        check = getattr(self.publisher, "check", None)
        if check:
            check()  # Kafka가 꺼져 있으면 KOVO에 요청하기 전에 멈춘다(받아 놓고 못 보내는 일을 막음)
        run_id = self.run_id = uuid.uuid4().hex
        record = self.logger.start(self.name, self.describe(**params), run_id)
        count = 0
        try:
            for endpoint, request_params, body in self.collect(**params):
                envelope = make_envelope(source=self.source, endpoint=endpoint, params=request_params,
                                         payload=body, collector=self.name, run_id=run_id)
                self.publisher.publish(self.topic, self.key_of(endpoint, request_params, body), envelope)
                count += 1
            self.publisher.flush()  # Kafka가 모든 데이터를 받았다고 확인해야 성공으로 친다
        except Exception as exc:
            self.logger.finish(record, "FAILED", count, f"{exc.__class__.__name__}: {exc}")
            try:
                self.publisher.flush()  # 실패 로그라도 전달되도록 한 번 더 비운다
            except Exception:
                log.exception("could not deliver the failure log for %s", self.name)
            raise
        self.logger.finish(record, "SUCCESS", count)
        self.publisher.flush()
        return count
