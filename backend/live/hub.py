"""Kafka live.metrics → 경기별로 열려 있는 SSE 스트림들에 나눠 준다(설계서 4.3, SSE 게이트웨이).

Uvicorn 프로세스마다 컨슈머 스레드 하나가 모든 파티션을 '끝에서부터' 읽는다(컨슈머 그룹 오프셋을 쓰지 않음:
새로 접속한 브라우저는 먼저 PostgreSQL에서 현재 상태를 받으므로 지난 메시지가 필요 없다).
열린 스트림마다 작은 asyncio 큐가 있다. 느린 브라우저가 뒤처지면 가장 오래된 갱신부터 버린다
(모든 갱신이 현재 상태 전체를 담고 있어서 최신 것만 있으면 되기 때문).

구조: Kafka 컨슈머 스레드 ──dispatch──▶ 이벤트 루프의 큐들 ──▶ 각 SSE 응답(views.stream)
"""
import asyncio
import logging
import os
import socket
import threading
import time
from collections import defaultdict

from django.conf import settings

log = logging.getLogger(__name__)
TOPIC = "live.metrics"
QUEUE_SIZE = 50  # 스트림마다 쌓아 둘 최대 갱신 수


class Hub:
    """경기 키별 구독자 목록을 관리하고, Kafka에서 온 메시지를 그 경기 구독자들의 큐에 넣는다."""

    def __init__(self, bootstrap=None, consumer_factory=None):
        """consumer_factory: 테스트에서 가짜 Kafka 컨슈머를 넣기 위한 것."""
        self.bootstrap = bootstrap or settings.KAFKA_BOOTSTRAP_SERVERS
        self.consumer_factory = consumer_factory
        self.subs = defaultdict(set)  # 경기 키 → {(이벤트 루프, 큐)}
        self.lock = threading.Lock()  # 컨슈머 스레드와 요청 처리 쪽이 subs를 함께 쓰므로 잠금
        self.thread = None
        self.connected = False

    def subscribe(self, key):
        """경기 구독: 새 큐를 만들어 등록하고 돌려준다(처음 구독할 때 컨슈머 스레드를 시작)."""
        self.start()
        q = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self.lock:
            self.subs[key].add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, key, q):
        """구독 해제. 그 경기 구독자가 없으면 키도 지운다."""
        with self.lock:
            self.subs[key] = {s for s in self.subs[key] if s[1] is not q}
            if not self.subs[key]:
                del self.subs[key]

    def streams(self):
        """지금 열린 스트림 수(상태 확인용)."""
        with self.lock:
            return sum(len(v) for v in self.subs.values())

    def start(self):
        """컨슈머 스레드를 한 번만 시작한다(데몬 스레드라 프로세스가 끝나면 함께 끝남)."""
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="live-metrics-consumer", daemon=True)
                self.thread.start()

    def dispatch(self, key, value):
        """메시지를 그 경기 구독자들의 큐에 넣는다. 큐는 이벤트 루프 소유라 call_soon_threadsafe로 그 루프에 맡긴다."""
        with self.lock:
            targets = list(self.subs.get(key, ()))
        for loop, q in targets:
            loop.call_soon_threadsafe(_put_latest, q, value)

    def _consumer(self):
        """Kafka 컨슈머를 만든다: 프로세스마다 고유한 그룹 id, 커밋 없음, 모든 파티션을 끝 위치에서 직접 배정."""
        if self.consumer_factory:
            return self.consumer_factory()
        from confluent_kafka import OFFSET_END, Consumer, TopicPartition

        c = Consumer({"bootstrap.servers": self.bootstrap,
                      "group.id": f"sse-gateway-{socket.gethostname()}-{os.getpid()}",
                      "enable.auto.commit": False, "auto.offset.reset": "latest"})
        partitions = c.list_topics(TOPIC, timeout=10).topics[TOPIC].partitions
        c.assign([TopicPartition(TOPIC, p, OFFSET_END) for p in partitions])
        return c

    def _run(self):
        """컨슈머 스레드 본체: 연결 → 메시지를 읽어 dispatch, 오류가 나면 잠시 쉬고 다시 연결(끝없이)."""
        while True:
            try:
                consumer = self._consumer()
            except Exception as exc:  # Kafka가 아직 안 떴다
                log.warning("live.metrics consumer: %s; retrying in 5 s", exc)
                time.sleep(5)
                continue
            self.connected = True
            log.info("reading %s", TOPIC)
            try:
                while True:
                    msg = consumer.poll(1.0)
                    if msg is None:
                        continue
                    if msg.error():
                        log.warning("live.metrics: %s", msg.error())
                        continue
                    if msg.key() and msg.value():
                        self.dispatch(msg.key().decode("utf-8"), msg.value().decode("utf-8"))
            except Exception as exc:
                log.warning("live.metrics consumer stopped: %s; reconnecting", exc)
                self.connected = False
                try:
                    consumer.close()
                except Exception:
                    pass
                time.sleep(2)


def _put_latest(q, value):
    """큐가 가득 찼으면 가장 오래된 것을 버리고 새 값을 넣는다."""
    if q.full():
        q.get_nowait()
    q.put_nowait(value)


hub = Hub()  # 프로세스당 하나만 쓰는 공용 객체
