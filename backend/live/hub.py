"""Kafka live.metrics -> the open SSE streams of each match (design.md 4.3, SSE gateway).

One consumer thread per Uvicorn process reads every partition from the end (no consumer group offsets: a
browser that connects gets the current state from PostgreSQL first, so old messages are never needed).
Each open stream has a small asyncio queue; when a slow browser falls behind, its oldest updates are dropped,
because every update carries the whole current state.
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
QUEUE_SIZE = 50


class Hub:
    def __init__(self, bootstrap=None, consumer_factory=None):
        self.bootstrap = bootstrap or settings.KAFKA_BOOTSTRAP_SERVERS
        self.consumer_factory = consumer_factory
        self.subs = defaultdict(set)  # match key -> {(loop, queue)}
        self.lock = threading.Lock()
        self.thread = None
        self.connected = False

    def subscribe(self, key):
        self.start()
        q = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self.lock:
            self.subs[key].add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, key, q):
        with self.lock:
            self.subs[key] = {s for s in self.subs[key] if s[1] is not q}
            if not self.subs[key]:
                del self.subs[key]

    def streams(self):
        with self.lock:
            return sum(len(v) for v in self.subs.values())

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="live-metrics-consumer", daemon=True)
                self.thread.start()

    def dispatch(self, key, value):
        with self.lock:
            targets = list(self.subs.get(key, ()))
        for loop, q in targets:
            loop.call_soon_threadsafe(_put_latest, q, value)

    def _consumer(self):
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
        while True:
            try:
                consumer = self._consumer()
            except Exception as exc:  # Kafka not up yet
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
    if q.full():
        q.get_nowait()
    q.put_nowait(value)


hub = Hub()
