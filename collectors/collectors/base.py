"""Common run loop for collectors (design.md 4.7 BaseCollector).

A subclass only says what to fetch (`collect`), which topic to use, and how to key each message.
The base class handles the run id, collection log, publishing, flushing and failure reporting.
"""
import logging
import uuid

from .collection_log import CollectionLogger
from .envelope import make_envelope

log = logging.getLogger(__name__)


class BaseCollector:
    name = "base"
    topic = ""

    def __init__(self, client, publisher):
        self.client = client
        self.publisher = publisher
        self.logger = CollectionLogger(publisher)

    # subclasses implement these
    def collect(self, **params):
        """Yield (endpoint, request_params, response_body) tuples."""
        raise NotImplementedError

    def key_of(self, endpoint: str, request_params: dict, body) -> str:
        raise NotImplementedError

    def describe(self, **params) -> str:
        return " ".join(f"{k}={v}" for k, v in params.items())

    def run(self, **params) -> int:
        check = getattr(self.publisher, "check", None)
        if check:
            check()  # no KOVO request is made if Kafka is down
        run_id = uuid.uuid4().hex
        record = self.logger.start(self.name, self.describe(**params), run_id)
        count = 0
        try:
            for endpoint, request_params, body in self.collect(**params):
                envelope = make_envelope(source="kovo", endpoint=endpoint, params=request_params,
                                         payload=body, collector=self.name, run_id=run_id)
                self.publisher.publish(self.topic, self.key_of(endpoint, request_params, body), envelope)
                count += 1
            self.publisher.flush()  # data must be acknowledged before the run counts as a success
        except Exception as exc:
            self.logger.finish(record, "FAILED", count, f"{exc.__class__.__name__}: {exc}")
            try:
                self.publisher.flush()
            except Exception:
                log.exception("could not deliver the failure log for %s", self.name)
            raise
        self.logger.finish(record, "SUCCESS", count)
        self.publisher.flush()
        return count
