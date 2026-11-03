import pytest
import requests

from collectors import kovo_client
from collectors.kovo_client import KovoApiError, KovoClient
from tests.fakes import FakeResponse, FakeSession, NoWaitLimiter, ok


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(kovo_client.time, "sleep", lambda s: None)


def make(responses):
    session = FakeSession(responses)
    limiter = NoWaitLimiter()
    return KovoClient(base_url="https://api.test", limiter=limiter, session=session), session, limiter


def test_every_request_goes_through_rate_limiter():
    client, session, limiter = make([ok([]), ok([])])
    client.season_list("001")
    client.season_list("002")
    assert limiter.waits == 2


def test_identifies_itself_and_sends_no_origin_header():
    client, session, _ = make([ok([])])
    assert session.headers["User-Agent"].startswith("volley-insight-collector")
    assert "Origin" not in session.headers


def test_schedule_params_drop_none_values():
    client, session, _ = make([ok({"content": []})])
    client.game_schedule("001", "023", "201", "1")
    url, params = session.calls[0]
    assert url == "https://api.test/stat/game-schedule"
    assert params == {"gcode": "001", "seasonCode": "023", "leagueCode": "201", "gender": "1", "page": 0, "size": 200}


def test_retries_on_server_error_then_succeeds():
    client, session, limiter = make([FakeResponse(503), ok([1])])
    assert client.season_list()["payload"] == [1]
    assert len(session.calls) == 2 and limiter.waits == 2


def test_retries_on_network_error():
    client, session, _ = make([requests.ConnectionError("down"), ok([])])
    client.season_list()
    assert len(session.calls) == 2


def test_gives_up_after_max_retries():
    client, session, _ = make([FakeResponse(503)] * 4)
    with pytest.raises(KovoApiError):
        client.season_list()
    assert len(session.calls) == 4


def test_client_error_is_not_retried():
    client, session, _ = make([FakeResponse(403)])
    with pytest.raises(KovoApiError, match="403"):
        client.season_list()
    assert len(session.calls) == 1


def test_body_level_error_status_raises():
    client, _, _ = make([FakeResponse(200, {"result": {"status": 400, "message": "bad"}, "payload": None})])
    with pytest.raises(KovoApiError, match="result.status=400"):
        client.season_list()
