"""시즌(SeasonCollector)·일정(ScheduleCollector) 수집기 테스트.

확인하는 것: 리그·성별 조합을 빠짐없이 도는지, 페이지를 따라가는지, 실행 기록(ops.collection-log)의
RUNNING → SUCCESS/FAILED 상태가 올바른지, Kafka가 죽어 있으면 KOVO에 요청하지 않는지.
"""
import pytest

from collectors.kovo_client import KovoApiError
from collectors.kovo_collectors import HonorCollector, ScheduleCollector, SeasonCollector, leagues_of
from tests.fakes import RecordingPublisher, ok

# 시즌 목록 응답. 022 시즌에는 정규리그(201)·플레이오프(202) 외에 실제 경기가 아닌 리그 코드
# (200 시즌 통산, 801 올스타전, 901 시범경기)도 섞여 있다 → leagues_of가 걸러야 한다
SEASONS = {"result": {"status": 200}, "payload": [
    {"seasonCode": "022", "leagues": [{"leagueCode": "201"}, {"leagueCode": "200"}, {"leagueCode": "202"},
                                      {"leagueCode": "801"}, {"leagueCode": "901"}]},
    {"seasonCode": "023", "leagues": [{"leagueCode": "201"}]},
]}


class FakeClient:
    """KovoClient 흉내. 일정은 페이지마다 경기 하나를 돌려주고, 전체 페이지 수는 pages로 정한다."""
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
    """실제 경기 리그 코드만 남기고, 없는 시즌은 빈 목록이다."""
    assert leagues_of(SEASONS, "022") == ["201", "202"]
    assert leagues_of(SEASONS, "999") == []


def test_schedule_covers_every_league_and_gender_with_season_key():
    """리그 2개 × 남녀 2개 = 4번 요청하고, 응답을 손대지 않은 채 시즌 코드를 키로 보낸다."""
    pub = RecordingPublisher()
    n = ScheduleCollector(FakeClient(), pub).run(season_code="022")
    assert n == 4  # 리그 2개 × 성별 2개
    msgs = pub.on("kovo.schedule")
    assert {m[1] for m in msgs} == {"022"}
    env = msgs[0][2]
    assert env["source"] == "kovo" and env["endpoint"] == "/stat/game-schedule"
    assert env["payload"]["payload"]["content"] == [{"gnum": 1}]  # 원본 응답을 그대로 담는다
    assert pub.flushed == 2  # 데이터를 먼저 확실히 보내고, 그다음 성공 기록


def test_schedule_follows_pagination():
    """totalPages만큼 페이지를 차례로 요청한다."""
    client = FakeClient(pages=3)
    ScheduleCollector(client, RecordingPublisher()).run(season_code="023", genders=("1",))
    assert [c[3] for c in client.schedule_calls] == [0, 1, 2]


def test_unknown_season_fails_and_is_logged():
    """없는 시즌이면 예외를 던지고 실행 기록은 RUNNING → FAILED로 남는다."""
    pub = RecordingPublisher()
    with pytest.raises(ValueError):
        ScheduleCollector(FakeClient(), pub).run(season_code="999")
    statuses = [m[2]["status"] for m in pub.on("ops.collection-log")]
    assert statuses == ["RUNNING", "FAILED"]


class FailingFlushPublisher(RecordingPublisher):
    """첫 flush(데이터 전송 확인)만 실패하는 발행기: 브로커가 잠깐 죽은 상황."""
    def flush(self, timeout=30.0):
        self.flushed += 1
        if self.flushed == 1:
            raise RuntimeError("broker down")


def test_failed_delivery_is_logged_once_as_failed_not_success():
    """데이터 전송이 실패하면 SUCCESS가 아니라 FAILED 한 번만 기록한다."""
    pub = FailingFlushPublisher()
    with pytest.raises(RuntimeError):
        SeasonCollector(FakeClient(), pub).run(gcode="001")
    statuses = [m[2]["status"] for m in pub.on("ops.collection-log")]
    assert statuses == ["RUNNING", "FAILED"]


class UnreachablePublisher(RecordingPublisher):
    """연결 확인(check)부터 실패하는 발행기: Kafka가 아예 꺼진 상황."""
    def check(self, timeout=10.0):
        raise RuntimeError("kafka down")


def test_no_kovo_request_when_kafka_is_down():
    """보낼 곳이 없으면 KOVO에 요청하지 않는다(받아 놓고 버리는 요청을 만들지 않기 위해)."""
    client = FakeClient()
    with pytest.raises(RuntimeError):
        ScheduleCollector(client, UnreachablePublisher()).run(season_code="023")
    assert client.schedule_calls == []


def test_collection_log_records_count_on_success():
    """성공하면 마지막 실행 기록이 SUCCESS이고 보낸 레코드 수가 담긴다."""
    pub = RecordingPublisher()
    SeasonCollector(FakeClient(), pub).run(gcode="001")
    done = pub.on("ops.collection-log")[-1][2]
    assert done["status"] == "SUCCESS" and done["record_count"] == 1


def test_schedule_collector_keeps_games_of_its_run_and_run_id():
    """수집한 경기 목록과 run_id를 객체에 남긴다(DAG가 다음 단계에서 쓰고, 메시지마다 같은 run_id가 붙는다)."""
    pub = RecordingPublisher()
    col = ScheduleCollector(FakeClient(), pub)
    col.run(season_code="023", genders=("1",))
    assert col.games == [{"gnum": 1}]
    assert col.run_id and all(m[2]["run_id"] == col.run_id for m in pub.on("kovo.schedule"))


class HonorClient:
    """선수 수상 API 흉내: 코드별로 응답 본문이나 예외를 정해 둔다."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def player_honor(self, code):
        self.asked.append(code)
        answer = self.answers[code]
        if isinstance(answer, Exception):
            raise answer
        return ok(answer).json()


def test_honor_collector_one_message_per_player_and_skips_unknown_codes():
    """선수마다 응답 하나를 선수 코드 키로 kovo.player에 보내고, KOVO에 없는 선수(404)는 건너뛴다."""
    honor = {"awards": ["2025~2026 V-리그 라운드 MVP (5R)"], "tripleCrowns": [], "standardRecords": []}
    client = HonorClient({"1000525": honor, "9999999": KovoApiError("not found", status=404), "0000463": honor})
    pub = RecordingPublisher()
    col = HonorCollector(client, pub)
    assert col.run(codes=["1000525", "9999999", "0000463"]) == 2
    assert [(m[1], m[2]["endpoint"]) for m in pub.on("kovo.player")] == [
        ("1000525", "/players/1000525/honor"), ("0000463", "/players/0000463/honor")]
    assert col.skipped == ["9999999"]
    assert pub.on("ops.collection-log")[-1][2]["target"] == "players=3 [1000525, 9999999, 0000463]"


def test_honor_collector_fails_on_other_errors():
    """404가 아닌 실패(예: KOVO 장애)는 건너뛰지 않고 실행을 실패로 남긴다."""
    pub = RecordingPublisher()
    with pytest.raises(KovoApiError):
        HonorCollector(HonorClient({"1": KovoApiError("down", status=503)}), pub).run(codes=["1"])
    assert pub.on("ops.collection-log")[-1][2]["status"] == "FAILED"
