"""수집기 테스트용 가짜 객체들(테스트 더블). 네트워크도 Kafka도 쓰지 않는다.

  FakeResponse        requests 응답 흉내(상태 코드 + JSON 본문)
  FakeSession         requests.Session 흉내: 미리 넣어 둔 응답을 차례로 돌려주고 호출을 기록
  NoWaitLimiter       요청 간격 제한기 흉내: 기다리지 않고 몇 번 불렸는지만 센다
  RecordingPublisher  Kafka 발행기 흉내: 보낸 메시지를 목록에 모은다
  ok(payload)         KOVO의 정상 응답 본문 모양으로 감싼 FakeResponse
"""


class FakeResponse:
    """requests 응답 흉내. 본문을 안 주면 KOVO의 빈 정상 응답을 쓴다."""
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"result": {"status": 200, "message": "OK"}, "payload": []}

    def json(self):
        return self._body


class FakeSession:
    """미리 넣어 둔 응답을 순서대로 돌려주고 모든 호출(url, params)을 기록한다.
    목록에 예외 객체를 넣으면 그 차례에 예외를 던진다(네트워크 오류 흉내)."""

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
    """요청 간격 제한기 흉내: 실제로 기다리지 않고 wait 호출 횟수만 센다(초당 1회 제한을 지키는지 확인용)."""
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


class RecordingPublisher:
    """Kafka 발행기 흉내. messages에 (토픽, 키, 값)이 쌓이고 flushed는 flush 호출 횟수다."""
    def __init__(self):
        self.messages = []
        self.flushed = 0

    def publish(self, topic, key, value):
        self.messages.append((topic, key, value))

    def flush(self, timeout=30.0):
        self.flushed += 1

    def on(self, topic):
        """특정 토픽으로 보낸 메시지만 골라 본다."""
        return [m for m in self.messages if m[0] == topic]


def ok(payload):
    """KOVO 정상 응답 모양({"result": {"status": 200}, "payload": ...})으로 감싼다."""
    return FakeResponse(200, {"result": {"status": 200, "message": "OK"}, "payload": payload})
