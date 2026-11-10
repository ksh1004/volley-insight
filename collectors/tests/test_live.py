"""실시간 수집기(LiveCollector)와 경기 재생기(MatchReplayer) 테스트.

FeedClient는 테스트가 feeds[세트]에 넣어 둔 이벤트를 그대로 돌려준다. 테스트가 feeds를 조금씩 늘려 가며
"경기가 진행되는" 상황을 만들고, 수집기가 새 이벤트만 보내는지, 세트를 넘어가는지, 끝을 아는지 본다.
FakeConn/FakeCursor는 오늘 경기 목록과 이어받을 위치(live_match_state)를 돌려주는 가짜 DB다.
"""
from datetime import datetime, timedelta, timezone

from collectors.live import LiveCollector, LiveGame, set_winner
from collectors.replay import MatchReplayer, rally_messages
from tests.fakes import RecordingPublisher, ok

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)  # 경기 예정 시각(한국 시각 19:00)


def ev(action, team="1004", grade=0, rally=1, result="tot"):
    """KOVO 실시간 피드의 이벤트 하나. action: ST 세트 시작, ET 세트 끝, AA 공격 등. grade 1 = 득점."""
    return {"raction": action, "rtcode": team, "rgrade": grade, "rpage": rally, "raresult": result, "rpcode": "0000000"}


def finished_set(home_points, away_points):
    """세트 하나의 전체 피드: 시작(ST) + 홈 득점들 + 원정 득점들 + 끝(ET)."""
    feed = [ev("ST", rally=0)]
    feed += [ev("AA", "1004", 1, i, "suc") for i in range(home_points)]
    feed += [ev("AA", "1008", 1, i, "suc") for i in range(away_points)]
    return feed + [ev("ET", rally=200)]


class FeedClient:
    """real_time()은 호출 시점에 feeds[세트]에 들어 있는 것을 돌려준다(페이지 단위로 잘라서)."""

    def __init__(self):
        self.feeds, self.calls = {}, []

    def real_time(self, gcode, season, gnum, hcode, acode, rset, page=0, size=500):
        self.calls.append((gnum, rset, page))
        return ok({"feed": self.feeds.get(rset, [])[page * size:(page + 1) * size]}).json()


class FakeCursor:
    """가짜 DB 커서. 경기 목록 조회면 games를, 이어받을 위치 조회면 resume["시즌-경기"]를 돌려준다."""

    def __init__(self, games, resume):
        self.games, self.resume, self.result = games, resume, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.result = self.games if "FROM match m" in sql and "live_match_state" not in sql else \
            [self.resume.get(f"{params[0]}-{params[1]}")] if self.resume.get(f"{params[0]}-{params[1]}") else []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class FakeConn:
    """가짜 DB 연결: 항상 같은 커서를 돌려준다."""

    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def close(self):
        pass


# 오늘 경기 한 줄: (시즌, 경기번호, gcode, 리그, 홈 팀, 원정 팀, 예정 시각)
GAME_ROW = ("023", 7, "001", "201", "1004", "1008", T0)


def collector(client, publisher, resume=None, now=T0):
    """가짜 DB와 고정 시계를 끼운 실시간 수집기."""
    cur = FakeCursor([GAME_ROW], resume or {})
    return LiveCollector(client, publisher, connect=lambda: FakeConn(cur), clock=lambda: now)


def live_messages(pub):
    """kovo.live 토픽으로 보낸 메시지만."""
    return list(pub.on("kovo.live"))


def test_sends_only_new_events_and_follows_sets_to_the_end():
    """매 회차에 새로 생긴 이벤트만 보내고, 세트가 끝나면 다음 세트로, 3세트를 먼저 따면 경기 종료로 본다."""
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub)
    c.refresh()
    c.round()                                    # 시작 전: 피드가 비어 보낼 것 없음
    assert live_messages(pub) == []
    client.feeds[1] = finished_set(3, 1)[:3]      # 세트 시작 + 2점
    c.round()
    client.feeds[1] = finished_set(3, 1)          # 1세트 나머지(세트 끝 ET 포함)
    c.round()
    sent = [m[2]["payload"] for m in live_messages(pub)]
    assert [(p["set"], p["start"], len(p["events"])) for p in sent] == [(1, 0, 3), (1, 3, 3)]
    assert sent[0]["mode"] == "LIVE" and live_messages(pub)[0][1] == "023-7"
    game = c.games["023-7"]
    assert (game.set_no, game.sent, game.winners) == (2, 0, {1: "home"})
    client.feeds[2], client.feeds[3] = finished_set(25, 20), finished_set(25, 23)
    c.round()
    c.round()
    assert game.done and game.sets == {"home": 3, "away": 0}
    finish = [m[2] for m in pub.on("ops.collection-log") if m[2]["status"] != "RUNNING"]
    assert finish[-1]["status"] == "SUCCESS" and finish[-1]["record_count"] == 6 + 47 + 50
    calls = len(client.calls)
    c.round()                                    # 경기 끝: 더는 요청하지 않음
    assert len(client.calls) == calls


def test_resumes_from_the_stored_position():
    """재시작해도 DB에 저장된 위치(2세트의 5번째 이벤트 = 2×10000+4)부터 이어서 보낸다."""
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub, resume={"023-7": (2 * 10000 + 4, [[25, 21]])})
    c.refresh()
    game = c.games["023-7"]
    assert (game.set_no, game.sent, game.winners) == (2, 5, {1: "home"})
    client.feeds[2] = finished_set(3, 3)[:7]
    c.round()
    assert [(m[2]["payload"]["start"], len(m[2]["payload"]["events"])) for m in live_messages(pub)] == [(5, 2)]


def test_events_are_resent_when_kafka_fails():
    """Kafka 전송이 실패하면 보낸 위치를 올리지 않아 다음 회차에 같은 이벤트를 다시 보낸다."""
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub)
    c.refresh()
    client.feeds[1] = finished_set(1, 0)[:2]
    fail = {"on": True}
    flush = pub.flush

    def flaky_flush(timeout=30.0):
        if fail["on"]:
            raise RuntimeError("broker down")
        flush(timeout)
    pub.flush = flaky_flush
    try:
        c.round()
    except RuntimeError:
        pass
    assert c.games["023-7"].sent == 0
    fail["on"] = False
    c.round()
    assert [m[2]["payload"]["start"] for m in live_messages(pub)] == [0, 0]  # 같은 이벤트를 다시 보냄. 중복은 Flink가 버린다


def test_gives_up_hours_after_the_start():
    """예정 시각에서 몇 시간(GIVE_UP)이 지나도 끝을 못 보면 FAILED로 기록하고 그 경기를 그만 본다."""
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub, now=T0 + timedelta(hours=5))
    c.games["023-7"] = LiveGame("023", 7, "001", "201", "1004", "1008", T0,
                                log_record={"run_id": "r", "job_name": "kovo.live"})
    c.round()
    assert c.games.get("023-7") is None or c.games["023-7"].done
    assert pub.on("ops.collection-log")[-1][2]["status"] == "FAILED"
    assert client.calls == []


def test_set_winner():
    """세트 피드에서 득점을 세어 홈/원정 중 세트를 딴 쪽을 고른다."""
    assert set_winner(finished_set(25, 23), "1004") == "home"
    assert set_winner(finished_set(13, 15), "1004") == "away"


def row(set_no, event_no, rally_no, action="AA", grade=0):
    """DB에 저장된 랠리 이벤트 한 행(재생기가 읽는 모양)."""
    return (set_no, event_no, rally_no, 1, "1004", None, None, None, action, "tot", grade)


def test_replay_sends_one_message_per_rally_with_feed_positions():
    """저장된 경기를 랠리 단위 메시지로 묶어 REPLAY 모드로 보내고, 세트 시간을 랠리 수와 배속으로 나눠 기다린다."""
    rows = [row(1, 1, 0, "ST"), row(1, 2, 1, "VV"), row(1, 3, 1, "AA", 1), row(1, 4, 2, "VS"), row(1, 5, 2, "AA", 1),
            row(1, 6, 200, "ET"), row(2, 1, 0, "ST"), row(2, 2, 1, "VV", 1)]
    msgs = rally_messages(rows)
    assert [(s, start, len(e)) for s, start, e in msgs] == [(1, 0, 1), (1, 1, 2), (1, 3, 2), (1, 5, 1), (2, 0, 1),
                                                            (2, 1, 1)]
    assert msgs[1][2][0]["rpcode"] == "0000000" and msgs[1][2][1]["rgrade"] == 1

    class Replayer(MatchReplayer):  # DB 대신 위의 rows를 읽는 재생기
        def load(self, key, from_set=1):
            return (1, "001", "201", "1004", "1008", "CONFIRMED"), rows, {1: 2, 2: 1}

    pub, sleeps = RecordingPublisher(), []
    n = Replayer(pub, connect=None, sleep=sleeps.append).replay("023-5", speed=60)
    assert n == 6
    payloads = [m[2]["payload"] for m in pub.on("kovo.live")]
    assert {p["mode"] for p in payloads} == {"REPLAY"} and len({p["session"] for p in payloads}) == 1
    assert pub.on("kovo.live")[0][2]["source"] == "replay"
    assert sleeps[0] == 2 * 60 / 4 / 60  # 1세트: 2분을 랠리 4개로 나누고, 60배속
