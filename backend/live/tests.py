"""실시간 경기 API와 SSE 스트림 테스트. 상태 행은 Flink live-metrics 작업이 쓰는 모양 그대로 직접 만든다.

  docker compose exec -T django-api python manage.py test live

SSE(Server-Sent Events): 브라우저가 연결을 열어 두면 서버가 "event: ...\ndata: ...\n\n" 형식으로 계속 밀어 준다.
StreamTests는 Kafka 대신 hub.dispatch()로 메시지를 직접 넣어 본다.
"""
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
    """022-10 경기가 2세트 12:9로 진행 중인 실시간 상태를 만든다(세트 지표 1줄, 득점 2개).
    updated를 주면 마지막 갱신 시각을 그 값으로 바꾼다(오래된 상태 흉내)."""
    make_league()
    m = Match.objects.get(game_no=10)  # 우리카드(홈 1009) vs 한국전력(원정 1006)
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
        """실시간 목록에 진행 중인 경기와 점수·서브권·승리 확률·세트 지표가 나온다."""
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
        """오래 갱신되지 않은 상태(1시간 전)는 목록에서 뺀다(수집이 멈춘 경기)."""
        make_live(updated=timezone.now() - timedelta(hours=1))
        assert self.client.get("/api/live").json()["results"] == []

    def test_detail_has_every_point(self):
        """경기 상세에는 모든 득점이 순서대로 있다. 없는 경기는 404."""
        make_live(mode="REPLAY")
        r = self.client.get("/api/live/022-10").json()
        assert r["state"]["mode"] == "REPLAY"
        assert [p["point_no"] for p in r["points"]] == [1, 2]
        assert r["points"][1]["winner"] == "1006"
        assert self.client.get("/api/live/022-99").status_code == 404

    def test_match_detail_links_to_live(self):
        """일반 경기 상세 API에 실시간 정보가 있으면 링크용 live 항목이 붙는다."""
        make_live()
        assert self.client.get("/api/matches/022-10").json()["live"]["mode"] == "LIVE"

    def test_stream_needs_the_asgi_gateway(self):
        """스트림은 ASGI 서버에서만 된다. 일반(WSGI) 요청이면 503."""
        make_live()
        assert self.client.get("/api/live/022-10/stream").status_code == 503


class StreamTests(TestCase):
    # 비동기 테스트: Django가 뷰의 DB 호출을 이 테스트의 연결·트랜잭션 안에서 실행하므로 make_live 데이터가 보인다
    async def test_snapshot_then_updates_from_kafka(self):
        """연결하면 재연결 간격(retry) → 현재 상태(snapshot) → 이후 이 경기의 갱신(update)만 받는다.
        브라우저가 떠나면(작업 취소) 허브에 등록된 스트림이 정리된다."""
        await sync_to_async(make_live)()
        hub = Hub(bootstrap="unused")
        hub.start = lambda: None  # 테스트에서는 Kafka 읽기 스레드를 띄우지 않는다
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
            hub.dispatch("022-11", '{"type": "update"}')  # 다른 경기: 이 스트림에는 안 온다
            update = (await asyncio.wait_for(anext(chunks), 2)).decode()
            assert update == 'event: update\ndata: {"type": "update", "state": {"home_points": 13}}\n\n'
            assert hub.streams() == 1
            # 브라우저가 떠남: Django의 ASGI 처리기가 다음 이벤트를 기다리던 작업을 취소한다
            waiting = asyncio.ensure_future(anext(chunks))
            await asyncio.sleep(0.05)
            waiting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiting
            assert hub.streams() == 0
