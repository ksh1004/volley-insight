from dataclasses import replace

import pytest

from collectors.kovo_client import KovoClient
from collectors.kovo_collectors import (Game, MatchCollector, RallyCollector, finished_games, is_finished)
from tests.fakes import FakeSession, NoWaitLimiter, RecordingPublisher, ok

SEASONS = {"result": {"status": 200}, "payload": [{"seasonCode": "022", "leagues": [{"leagueCode": "201"}]}]}


def item(gnum, hs, as_, result):
    return {"seasonCode": "022", "leagueCode": "201", "gnum": gnum, "hcode": "1006", "acode": "1009",
            "hspoint": hs, "aspoint": as_, "result": result}


class FakeClient:
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


GAME = Game(season="022", league="201", gnum=3, hcode="1006", acode="1009", sets=4)


def test_is_finished_needs_result_and_three_sets():
    assert is_finished(item(3, 0, 3, "L"))
    assert is_finished(item(6, 3, 2, "W"))
    assert not is_finished(item(5, 0, 0, ""))  # scheduled, not played yet
    assert not is_finished(item(7, 1, 0, ""))  # in progress


def test_finished_games_reads_schedule_and_counts_sets():
    client = FakeClient(schedule=[item(6, 3, 2, "W"), item(5, 0, 0, ""), item(3, 0, 3, "L")])
    games = finished_games(client, "022")
    assert [(g.key, g.sets) for g in games] == [("022-3", 3), ("022-6", 5)]


def test_match_collector_one_message_per_game_keyed_by_season_and_gnum():
    client, pub = FakeClient(), RecordingPublisher()
    n = MatchCollector(client, pub).run(games=[GAME], gcode="001")
    assert n == 1 and client.detail_calls == [("022", "201", 3)]
    topic, key, env = pub.on("kovo.match")[0]
    assert key == "022-3" and env["endpoint"] == "/stat/game-schedule/3"
    assert env["params"] == {"gcode": "001", "seasonCode": "022", "leagueCode": "201", "gnum": 3}


def test_rally_collector_requests_every_played_set():
    client, pub = FakeClient(events_per_set=280), RecordingPublisher()
    n = RallyCollector(client, pub).run(games=[GAME])
    assert client.rt_calls == [(3, 1, 0), (3, 2, 0), (3, 3, 0), (3, 4, 0)]  # 4 sets, one page each
    assert n == 4 and {m[1] for m in pub.on("kovo.rally")} == {"022-3"}


def test_rally_collector_follows_pages_until_a_short_page():
    client = FakeClient(events_per_set=294)
    RallyCollector(client, RecordingPublisher()).run(games=[replace(GAME, sets=1)], size=200)
    assert client.rt_calls == [(3, 1, 0), (3, 1, 1)]


def test_rally_collector_stops_on_exact_multiple_with_empty_page():
    client = FakeClient(events_per_set=400)
    RallyCollector(client, RecordingPublisher()).run(games=[replace(GAME, sets=1)], size=200)
    assert client.rt_calls == [(3, 1, 0), (3, 1, 1), (3, 1, 2)]


def test_collection_log_target_summarises_games():
    pub = RecordingPublisher()
    MatchCollector(FakeClient(), pub).run(games=[GAME], gcode="001")
    assert pub.on("ops.collection-log")[0][2]["target"] == "gcode=001 games=1 [022-3]"


def test_client_rejects_rewritten_paths():
    client = KovoClient(base_url="https://example.test", limiter=NoWaitLimiter(), session=FakeSession([]))
    with pytest.raises(ValueError):
        client.get("C:/Program Files/Git/stat/real-time")


def test_real_time_request_parameters():
    session = FakeSession([ok({"feed": []})])
    KovoClient(base_url="https://example.test", limiter=NoWaitLimiter(), session=session) \
        .real_time("001", "022", 3, "1006", "1009", rset=2)
    url, params = session.calls[0]
    assert url == "https://example.test/stat/real-time"
    assert params == {"season": "022", "gcode": "001", "gnum": 3, "hcode": "1006", "acode": "1009",
                      "rset": 2, "page": 0, "size": 500}


def test_rally_collector_skips_seasons_without_play_by_play():
    client, pub = FakeClient(), RecordingPublisher()
    n = RallyCollector(client, pub).run(games=[replace(GAME, season="019")])
    assert n == 0 and client.rt_calls == []


def test_rally_collector_stops_a_game_when_set_one_is_empty():
    client = FakeClient(events_per_set=0)  # KOVO has not published this game's play-by-play yet
    n = RallyCollector(client, RecordingPublisher()).run(games=[GAME, replace(GAME, gnum=4)])
    assert client.rt_calls == [(3, 1, 0), (4, 1, 0)] and n == 2
