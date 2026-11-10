"""KovoClient(KOVO API 호출 담당) 테스트: 요청 간격 제한, 자기 소개 헤더, 파라미터, 재시도 규칙.

가짜 세션(FakeSession)이 미리 정한 응답을 돌려주므로 실제 KOVO에는 요청하지 않는다.
"""
import pytest
import requests

from collectors import kovo_client
from collectors.kovo_client import KovoApiError, KovoClient
from tests.fakes import FakeResponse, FakeSession, NoWaitLimiter, ok


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """재시도 사이의 대기(time.sleep)를 없애 테스트가 바로 끝나게 한다(모든 테스트에 자동 적용)."""
    monkeypatch.setattr(kovo_client.time, "sleep", lambda s: None)


def make(responses):
    """가짜 세션·제한기를 끼운 클라이언트를 만든다. 반환: (클라이언트, 세션, 제한기)."""
    session = FakeSession(responses)
    limiter = NoWaitLimiter()
    return KovoClient(base_url="https://api.test", limiter=limiter, session=session), session, limiter


def test_every_request_goes_through_rate_limiter():
    """모든 요청이 간격 제한기를 거친다(KOVO에는 초당 1회까지만 요청한다)."""
    client, session, limiter = make([ok([]), ok([])])
    client.season_list("001")
    client.season_list("002")
    assert limiter.waits == 2


def test_identifies_itself_and_sends_no_origin_header():
    """User-Agent로 수집기임을 밝히고, 브라우저인 척하는 Origin 헤더는 보내지 않는다."""
    client, session, _ = make([ok([])])
    assert session.headers["User-Agent"].startswith("volley-insight-collector")
    assert "Origin" not in session.headers


def test_schedule_params_drop_none_values():
    """일정 요청의 주소와 파라미터. 값이 None인 파라미터는 보내지 않는다."""
    client, session, _ = make([ok({"content": []})])
    client.game_schedule("001", "023", "201", "1")
    url, params = session.calls[0]
    assert url == "https://api.test/stat/game-schedule"
    assert params == {"gcode": "001", "seasonCode": "023", "leagueCode": "201", "gender": "1", "page": 0, "size": 200}


def test_retries_on_server_error_then_succeeds():
    """503 같은 서버 오류는 다시 시도하고, 재시도도 간격 제한기를 거친다."""
    client, session, limiter = make([FakeResponse(503), ok([1])])
    assert client.season_list()["payload"] == [1]
    assert len(session.calls) == 2 and limiter.waits == 2


def test_retries_on_network_error():
    """연결 실패 같은 네트워크 오류도 다시 시도한다."""
    client, session, _ = make([requests.ConnectionError("down"), ok([])])
    client.season_list()
    assert len(session.calls) == 2


def test_gives_up_after_max_retries():
    """최대 횟수(처음 1번 + 재시도 3번)를 넘기면 KovoApiError로 포기한다."""
    client, session, _ = make([FakeResponse(503)] * 4)
    with pytest.raises(KovoApiError):
        client.season_list()
    assert len(session.calls) == 4


def test_client_error_is_not_retried():
    """403 같은 클라이언트 오류는 다시 해도 같으므로 재시도하지 않는다."""
    client, session, _ = make([FakeResponse(403)])
    with pytest.raises(KovoApiError, match="403"):
        client.season_list()
    assert len(session.calls) == 1


def test_body_level_error_status_raises():
    """HTTP 200이어도 본문의 result.status가 200이 아니면 실패로 본다."""
    client, _, _ = make([FakeResponse(200, {"result": {"status": 400, "message": "bad"}, "payload": None})])
    with pytest.raises(KovoApiError, match="result.status=400"):
        client.season_list()
