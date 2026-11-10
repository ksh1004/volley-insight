"""경기 결과(MatchCollector)·랠리 기록(RallyCollector) 수집기와 KovoClient 요청 모양 테스트.

FakeClient가 KOVO API를 흉내 낸다: 일정은 남자부(gender "1")에만 경기를 돌려주고,
real_time(랠리 기록)은 세트마다 events_per_set개의 이벤트를 size개씩 페이지로 나눠 준다.
"""
from dataclasses import replace

import pytest

from collectors.kovo_client import KovoClient
from collectors.kovo_collectors import (Game, MatchCollector, RallyCollector, finished_games, is_finished)
from tests.fakes import FakeSession, NoWaitLimiter, RecordingPublisher, ok

# 시즌 목록 응답: 022 시즌에 정규리그(201) 하나
SEASONS = {"result": {"status": 200}, "payload": [{"seasonCode": "022", "leagues": [{"leagueCode": "201"}]}]}


def item(gnum, hs, as_, result):
    """KOVO 일정 응답의 경기 한 줄. hs/as_는 홈/원정이 이긴 세트 수, result가 비면 아직 결과 없음."""
    return {"seasonCode": "022", "leagueCode": "201", "gnum": gnum, "hcode": "1006", "acode": "1009",
            "hspoint": hs, "aspoint": as_, "result": result}


class FakeClient:
    """KovoClient 흉내. 경기 상세(detail_calls)와 랠리 기록(rt_calls) 요청을 기록한다."""
    def __init__(self, schedule=(), events_per_set=3):
        self.schedule = list(schedule)
        self.events_per_set = events_per_set
        self.detail_calls, self.rt_calls = [], []

    def season_list(self, gcode="001", season_code=None):
        return SEASONS

    def game_schedule(self, gcode, season_code, league_code, gender, page=0, size=200):
        content = self.schedule if gender == "1" else []
        return ok({"content": content, "page": {"totalPages": 1}}).json()

    def game_detail(self, gcode, season_code, league_code, gnum):
        self.detail_calls.append((season_code, league_code, gnum))
        return ok({"game": {"gnum": gnum}, "player": [], "team": []}).json()

    def real_time(self, gcode, season_code, gnum, hcode, acode, rset, page=0, size=500):
        self.rt_calls.append((gnum, rset, page))
        left = self.events_per_set - page * size
        return ok({"feed": [{"rpage": i} for i in range(max(0, min(size, left)))]}).json()


# 테스트용 경기: 022 시즌 3번 경기, 4세트까지 함
GAME = Game(season="022", league="201", gnum=3, hcode="1006", acode="1009", sets=4)


def test_is_finished_needs_result_and_three_sets():
    """결과가 있고 세트가 3개 이상이어야 끝난 경기다."""
    assert is_finished(item(3, 0, 3, "L"))
    assert is_finished(item(6, 3, 2, "W"))
    assert not is_finished(item(5, 0, 0, ""))  # 예정, 아직 안 함
    assert not is_finished(item(7, 1, 0, ""))  # 진행 중


def test_finished_games_reads_schedule_and_counts_sets():
    """일정에서 끝난 경기만 경기번호 순으로 고르고, 세트 수(=랠리 기록을 받을 세트 수)를 계산한다."""
    client = FakeClient(schedule=[item(6, 3, 2, "W"), item(5, 0, 0, ""), item(3, 0, 3, "L")])
    games = finished_games(client, "022")
    assert [(g.key, g.sets) for g in games] == [("022-3", 3), ("022-6", 5)]


def test_match_collector_one_message_per_game_keyed_by_season_and_gnum():
    """경기마다 상세 응답 하나를 "시즌-경기번호" 키로 kovo.match 토픽에 보낸다(같은 경기는 같은 파티션)."""
    client, pub = FakeClient(), RecordingPublisher()
    n = MatchCollector(client, pub).run(games=[GAME], gcode="001")
    assert n == 1 and client.detail_calls == [("022", "201", 3)]
    topic, key, env = pub.on("kovo.match")[0]
    assert key == "022-3" and env["endpoint"] == "/stat/game-schedule/3"
    assert env["params"] == {"gcode": "001", "seasonCode": "022", "leagueCode": "201", "gnum": 3}


def test_rally_collector_requests_every_played_set():
    """경기한 세트마다 랠리 기록을 요청한다(세트당 280개 < 페이지 크기 500이라 한 페이지씩)."""
    client, pub = FakeClient(events_per_set=280), RecordingPublisher()
    n = RallyCollector(client, pub).run(games=[GAME])
    assert client.rt_calls == [(3, 1, 0), (3, 2, 0), (3, 3, 0), (3, 4, 0)]  # 4세트, 세트당 한 페이지
    assert n == 4 and {m[1] for m in pub.on("kovo.rally")} == {"022-3"}


def test_rally_collector_follows_pages_until_a_short_page():
    """294개를 200개씩 받으면 두 번째 페이지가 꽉 차지 않으므로 거기서 멈춘다."""
    client = FakeClient(events_per_set=294)
    RallyCollector(client, RecordingPublisher()).run(games=[replace(GAME, sets=1)], size=200)
    assert client.rt_calls == [(3, 1, 0), (3, 1, 1)]


def test_rally_collector_stops_on_exact_multiple_with_empty_page():
    """400개처럼 페이지 크기의 배수면 빈 세 번째 페이지를 받고 나서야 끝을 안다."""
    client = FakeClient(events_per_set=400)
    RallyCollector(client, RecordingPublisher()).run(games=[replace(GAME, sets=1)], size=200)
    assert client.rt_calls == [(3, 1, 0), (3, 1, 1), (3, 1, 2)]


def test_collection_log_target_summarises_games():
    """수집 실행 기록(ops.collection-log)의 target에 대상 경기를 요약해 남긴다."""
    pub = RecordingPublisher()
    MatchCollector(FakeClient(), pub).run(games=[GAME], gcode="001")
    assert pub.on("ops.collection-log")[0][2]["target"] == "gcode=001 games=1 [022-3]"


def test_client_rejects_rewritten_paths():
    """Git Bash가 "/stat/..."를 Windows 경로로 바꿔 버린 경우를 잡아낸다(경로는 "/"로 시작해야 함)."""
    client = KovoClient(base_url="https://example.test", limiter=NoWaitLimiter(), session=FakeSession([]))
    with pytest.raises(ValueError):
        client.get("C:/Program Files/Git/stat/real-time")


def test_real_time_request_parameters():
    """랠리 기록 API를 KOVO 사이트와 같은 주소·파라미터로 부른다."""
    session = FakeSession([ok({"feed": []})])
    KovoClient(base_url="https://example.test", limiter=NoWaitLimiter(), session=session) \
        .real_time("001", "022", 3, "1006", "1009", rset=2)
    url, params = session.calls[0]
    assert url == "https://example.test/stat/real-time"
    assert params == {"season": "022", "gcode": "001", "gnum": 3, "hcode": "1006", "acode": "1009",
                      "rset": 2, "page": 0, "size": 500}


def test_rally_collector_skips_seasons_without_play_by_play():
    """KOVO가 랠리 기록을 제공하지 않는 옛 시즌(019)은 요청 자체를 하지 않는다."""
    client, pub = FakeClient(), RecordingPublisher()
    n = RallyCollector(client, pub).run(games=[replace(GAME, season="019")])
    assert n == 0 and client.rt_calls == []


def test_rally_collector_stops_a_game_when_set_one_is_empty():
    """1세트 기록이 비어 있으면 그 경기의 나머지 세트는 건너뛰고 다음 경기로 간다(빈 응답도 기록은 보낸다)."""
    client = FakeClient(events_per_set=0)  # KOVO가 아직 이 경기의 랠리 기록을 올리지 않은 상황
    n = RallyCollector(client, RecordingPublisher()).run(games=[GAME, replace(GAME, gnum=4)])
    assert client.rt_calls == [(3, 1, 0), (4, 1, 0)] and n == 2
