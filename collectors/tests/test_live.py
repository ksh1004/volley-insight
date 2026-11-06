from datetime import datetime, timedelta, timezone

from collectors.live import LiveCollector, LiveGame, set_winner
from collectors.replay import MatchReplayer, rally_messages
from tests.fakes import RecordingPublisher, ok

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def ev(action, team="1004", grade=0, rally=1, result="tot"):
    return {"raction": action, "rtcode": team, "rgrade": grade, "rpage": rally, "raresult": result, "rpcode": "0000000"}


def finished_set(home_points, away_points):
    feed = [ev("ST", rally=0)]
    feed += [ev("AA", "1004", 1, i, "suc") for i in range(home_points)]
    feed += [ev("AA", "1008", 1, i, "suc") for i in range(away_points)]
    return feed + [ev("ET", rally=200)]


class FeedClient:
    """real_time() returns whatever `feeds[set]` holds at the time of the call."""

    def __init__(self):
        self.feeds, self.calls = {}, []

    def real_time(self, gcode, season, gnum, hcode, acode, rset, page=0, size=500):
        self.calls.append((gnum, rset, page))
        return ok({"feed": self.feeds.get(rset, [])[page * size:(page + 1) * size]}).json()


class FakeCursor:
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


GAME_ROW = ("023", 7, "001", "201", "1004", "1008", T0)


def collector(client, publisher, resume=None, now=T0):
    cur = FakeCursor([GAME_ROW], resume or {})
    return LiveCollector(client, publisher, connect=lambda: FakeConn(cur), clock=lambda: now)


def live_messages(pub):
    return [m for m in pub.on("kovo.live")]


def test_sends_only_new_events_and_follows_sets_to_the_end():
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub)
    c.refresh()
    c.round()                                    # not started: empty feed, nothing sent
    assert live_messages(pub) == []
    client.feeds[1] = finished_set(3, 1)[:3]      # ST + two points
    c.round()
    client.feeds[1] = finished_set(3, 1)          # rest of set 1, including ET
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
    c.round()                                    # finished: no more requests
    assert len(client.calls) == calls


def test_resumes_from_the_stored_position():
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub, resume={"023-7": (2 * 10000 + 4, [[25, 21]])})
    c.refresh()
    game = c.games["023-7"]
    assert (game.set_no, game.sent, game.winners) == (2, 5, {1: "home"})
    client.feeds[2] = finished_set(3, 3)[:7]
    c.round()
    assert [(m[2]["payload"]["start"], len(m[2]["payload"]["events"])) for m in live_messages(pub)] == [(5, 2)]


def test_events_are_resent_when_kafka_fails():
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
    assert [m[2]["payload"]["start"] for m in live_messages(pub)] == [0, 0]  # same events again; Flink drops repeats


def test_gives_up_hours_after_the_start():
    client, pub = FeedClient(), RecordingPublisher()
    c = collector(client, pub, now=T0 + timedelta(hours=5))
    c.games["023-7"] = LiveGame("023", 7, "001", "201", "1004", "1008", T0,
                                log_record={"run_id": "r", "job_name": "kovo.live"})
    c.round()
    assert c.games.get("023-7") is None or c.games["023-7"].done
    assert pub.on("ops.collection-log")[-1][2]["status"] == "FAILED"
    assert client.calls == []


def test_set_winner():
    assert set_winner(finished_set(25, 23), "1004") == "home"
    assert set_winner(finished_set(13, 15), "1004") == "away"


def row(set_no, event_no, rally_no, action="AA", grade=0):
    return (set_no, event_no, rally_no, 1, "1004", None, None, None, action, "tot", grade)


def test_replay_sends_one_message_per_rally_with_feed_positions():
    rows = [row(1, 1, 0, "ST"), row(1, 2, 1, "VV"), row(1, 3, 1, "AA", 1), row(1, 4, 2, "VS"), row(1, 5, 2, "AA", 1),
            row(1, 6, 200, "ET"), row(2, 1, 0, "ST"), row(2, 2, 1, "VV", 1)]
    msgs = rally_messages(rows)
    assert [(s, start, len(e)) for s, start, e in msgs] == [(1, 0, 1), (1, 1, 2), (1, 3, 2), (1, 5, 1), (2, 0, 1),
                                                            (2, 1, 1)]
    assert msgs[1][2][0]["rpcode"] == "0000000" and msgs[1][2][1]["rgrade"] == 1

    class Replayer(MatchReplayer):
        def load(self, key, from_set=1):
            return (1, "001", "201", "1004", "1008", "CONFIRMED"), rows, {1: 2, 2: 1}

    pub, sleeps = RecordingPublisher(), []
    n = Replayer(pub, connect=None, sleep=sleeps.append).replay("023-5", speed=60)
    assert n == 6
    payloads = [m[2]["payload"] for m in pub.on("kovo.live")]
    assert {p["mode"] for p in payloads} == {"REPLAY"} and len({p["session"] for p in payloads}) == 1
    assert pub.on("kovo.live")[0][2]["source"] == "replay"
    assert sleeps[0] == 2 * 60 / 4 / 60  # set 1: 2 minutes over 4 rallies, 60x
