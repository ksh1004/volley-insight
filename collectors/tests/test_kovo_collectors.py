import pytest

from collectors.kovo_collectors import ScheduleCollector, SeasonCollector, leagues_of
from tests.fakes import RecordingPublisher, ok

SEASONS = {"result": {"status": 200}, "payload": [
    {"seasonCode": "022", "leagues": [{"leagueCode": "201"}, {"leagueCode": "200"}, {"leagueCode": "202"},
                                      {"leagueCode": "801"}, {"leagueCode": "901"}]},
    {"seasonCode": "023", "leagues": [{"leagueCode": "201"}]},
]}


class FakeClient:
    def __init__(self, pages=1):
        self.pages = pages
        self.schedule_calls = []

    def season_list(self, gcode="001", season_code=None):
        return SEASONS

    def game_schedule(self, gcode, season_code, league_code, gender, page=0, size=200):
        self.schedule_calls.append((season_code, league_code, gender, page))
        return ok({"content": [{"gnum": page + 1}],
                   "page": {"size": size, "number": page, "totalPages": self.pages}}).json()


def test_leagues_of_skips_all_star_codes():
    assert leagues_of(SEASONS, "022") == ["201", "202"]
    assert leagues_of(SEASONS, "999") == []


def test_schedule_covers_every_league_and_gender_with_season_key():
    pub = RecordingPublisher()
    n = ScheduleCollector(FakeClient(), pub).run(season_code="022")
    assert n == 4  # 2 leagues x 2 genders
    msgs = pub.on("kovo.schedule")
    assert {m[1] for m in msgs} == {"022"}
    env = msgs[0][2]
    assert env["source"] == "kovo" and env["endpoint"] == "/stat/game-schedule"
    assert env["payload"]["payload"]["content"] == [{"gnum": 1}]  # raw response kept as-is
    assert pub.flushed == 2  # data first, then the success log


def test_schedule_follows_pagination():
    client = FakeClient(pages=3)
    ScheduleCollector(client, RecordingPublisher()).run(season_code="023", genders=("1",))
    assert [c[3] for c in client.schedule_calls] == [0, 1, 2]


def test_unknown_season_fails_and_is_logged():
    pub = RecordingPublisher()
    with pytest.raises(ValueError):
        ScheduleCollector(FakeClient(), pub).run(season_code="999")
    statuses = [m[2]["status"] for m in pub.on("ops.collection-log")]
    assert statuses == ["RUNNING", "FAILED"]


class FailingFlushPublisher(RecordingPublisher):
    def flush(self, timeout=30.0):
        self.flushed += 1
        if self.flushed == 1:
            raise RuntimeError("broker down")


def test_failed_delivery_is_logged_once_as_failed_not_success():
    pub = FailingFlushPublisher()
    with pytest.raises(RuntimeError):
        SeasonCollector(FakeClient(), pub).run(gcode="001")
    statuses = [m[2]["status"] for m in pub.on("ops.collection-log")]
    assert statuses == ["RUNNING", "FAILED"]


class UnreachablePublisher(RecordingPublisher):
    def check(self, timeout=10.0):
        raise RuntimeError("kafka down")


def test_no_kovo_request_when_kafka_is_down():
    client = FakeClient()
    with pytest.raises(RuntimeError):
        ScheduleCollector(client, UnreachablePublisher()).run(season_code="023")
    assert client.schedule_calls == []


def test_collection_log_records_count_on_success():
    pub = RecordingPublisher()
    SeasonCollector(FakeClient(), pub).run(gcode="001")
    done = pub.on("ops.collection-log")[-1][2]
    assert done["status"] == "SUCCESS" and done["record_count"] == 1
