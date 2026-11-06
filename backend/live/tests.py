"""Live API and SSE gateway tests (state rows as Flink live-metrics writes them)."""
import asyncio
import contextlib
import json
from datetime import timedelta
from unittest import mock

from asgiref.sync import sync_to_async

from django.test import AsyncClient, TestCase
from django.utils import timezone

from matches.models import Match
from matches.tests import make_league

from .hub import Hub
from .models import LiveMatchState, LiveRally, LiveSetMetric


def make_live(mode="LIVE", status="LIVE", updated=None):
    make_league()
    m = Match.objects.get(game_no=10)  # 우리카드 (home 1009) vs 한국전력 (away 1006)
    st = LiveMatchState.objects.create(match=m, mode=mode, session="live", status=status, current_set=2,
                                       home_points=12, away_points=9, home_sets=1, away_sets=0, set_scores=[[25, 21]],
                                       serving_team=m.home_team, home_run=3, home_win_prob="0.8123",
                                       home_set_win_prob="0.7700", last_event_seq=20087)
    if updated:
        LiveMatchState.objects.filter(pk=st.pk).update(updated_at=updated)
    LiveSetMetric.objects.create(match=m, set_no=2, team=m.home_team, points=12, attack_attempts=20, attack_success=9,
                                 attack_success_rate="45.00", serve_aces=1, block_points=2, errors=3)
    for n, (h, a) in enumerate([(1, 0), (1, 1)], 1):
        LiveRally.objects.create(match=m, set_no=2, point_no=n, rally_no=n, seq=20000 + n, home_score=h, away_score=a,
                                 winner_team=m.home_team if h > a or n == 1 else m.away_team, kind="ATTACK",
                                 player_name="아라우조", action_code="AA", result_code="suc")
    return m


class LiveApiTests(TestCase):
    def test_list_shows_followed_games_with_state(self):
        make_live()
        r = self.client.get("/api/live").json()
        assert len(r["results"]) == 1
        item = r["results"][0]
        assert item["match"]["key"] == "022-10"
        s = item["state"]
        assert (s["home"], s["away"], s["serving"]) == ("1009", "1006", "1009")
        assert s["set_open"] is True and s["home_win_prob"] == 0.8123
        assert s["metrics"][0]["side"] == "home" and s["metrics"][0]["attack_success_rate"] == 45.0

    def test_list_drops_stale_rows(self):
        make_live(updated=timezone.now() - timedelta(hours=1))
        assert self.client.get("/api/live").json()["results"] == []

    def test_detail_has_every_point(self):
        make_live(mode="REPLAY")
        r = self.client.get("/api/live/022-10").json()
        assert r["state"]["mode"] == "REPLAY"
        assert [p["point_no"] for p in r["points"]] == [1, 2]
        assert r["points"][1]["winner"] == "1006"
        assert self.client.get("/api/live/022-99").status_code == 404

    def test_match_detail_links_to_live(self):
        make_live()
        assert self.client.get("/api/matches/022-10").json()["live"]["mode"] == "LIVE"

    def test_stream_needs_the_asgi_gateway(self):
        make_live()
        assert self.client.get("/api/live/022-10/stream").status_code == 503


class StreamTests(TestCase):
    # async test: Django runs the view's database calls on this test's connection, inside its transaction
    async def test_snapshot_then_updates_from_kafka(self):
        await sync_to_async(make_live)()
        hub = Hub(bootstrap="unused")
        hub.start = lambda: None  # no Kafka thread in tests
        with mock.patch("live.views.hub", hub):
            response = await AsyncClient().get("/api/live/022-10/stream")
            assert response["Content-Type"].startswith("text/event-stream")
            chunks = aiter(response.streaming_content)
            assert (await anext(chunks)).startswith(b"retry:")
            snap = (await anext(chunks)).decode()
            assert snap.startswith("event: snapshot")
            data = json.loads(snap.split("data: ", 1)[1])
            assert data["state"]["home_points"] == 12 and len(data["points"]) == 2
            hub.dispatch("022-10", '{"type": "update", "state": {"home_points": 13}}')
            hub.dispatch("022-11", '{"type": "update"}')  # another match: not sent here
            update = (await asyncio.wait_for(anext(chunks), 2)).decode()
            assert update == 'event: update\ndata: {"type": "update", "state": {"home_points": 13}}\n\n'
            assert hub.streams() == 1
            # the browser leaves: Django's ASGI handler cancels the task waiting for the next event
            waiting = asyncio.ensure_future(anext(chunks))
            await asyncio.sleep(0.05)
            waiting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiting
            assert hub.streams() == 0
