"""Test doubles: no network, no Kafka."""


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"result": {"status": 200, "message": "OK"}, "payload": []}

    def json(self):
        return self._body


class FakeSession:
    """Returns queued responses in order and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NoWaitLimiter:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


class RecordingPublisher:
    def __init__(self):
        self.messages = []
        self.flushed = 0

    def publish(self, topic, key, value):
        self.messages.append((topic, key, value))

    def flush(self, timeout=30.0):
        self.flushed += 1

    def on(self, topic):
        return [m for m in self.messages if m[0] == topic]


def ok(payload):
    return FakeResponse(200, {"result": {"status": 200, "message": "OK"}, "payload": payload})
