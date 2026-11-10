"""실시간 경기 API(설계서 1.11, WEB-04).

  GET /api/live                 지금 따라가는 경기들(실시간·재생)과 각 경기의 현재 상태
  GET /api/live/{key}           경기 하나의 상태, 세트 지표, 지금까지의 모든 득점
  GET /api/live/{key}/stream    SSE(Server-Sent Events): 처음에 `snapshot` 한 번, 그다음 live.metrics 메시지마다 `update`
                                (Uvicorn 위의 django-sse 서비스가 처리. compose.yaml 참고)

  SSE = 서버가 브라우저로 이벤트를 계속 밀어 주는 HTTP 연결(브라우저는 EventSource로 받음). 웹소켓보다 단순하고,
  끊기면 브라우저가 알아서 다시 연결한다.
"""
import asyncio
import json
import re

from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.http import Http404, JsonResponse, StreamingHttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from matches.models import Match
from matches.views import SOURCE, base_matches, get_match

from . import queries
from .hub import hub

HEARTBEAT_SEC = 15  # 이벤트가 없어도 15초마다 신호를 보내, 중간 프록시가 조용한 연결을 끊지 않게 한다


class LiveListView(APIView):
    """GET /api/live: 홈 화면의 '진행 중 경기' 카드들."""

    def get(self, request):
        return Response({"results": queries.current(), "source": SOURCE})


class LiveDetailView(APIView):
    """GET /api/live/{key}: 실시간 화면을 처음 열 때(또는 스트림을 못 쓸 때) 받는 전체 상태."""

    def get(self, request, key):
        return Response({**queries.snapshot(get_match(key)), "source": SOURCE})


def _find_match(key):
    """경기 키로 경기를 찾는다(없으면 404). 비동기 뷰에서 sync_to_async로 감싸 부른다."""
    m = re.fullmatch(r"(\d{3})-(\d+)", key)
    try:
        return base_matches().get(competition__season__code=m[1], game_no=int(m[2]))
    except Match.DoesNotExist:
        raise Http404("no such match")


def sse(event, data: str):
    """SSE 형식 한 덩어리: "event: 이름" + "data: 내용" + 빈 줄."""
    return f"event: {event}\ndata: {data}\n\n"


async def stream(request, key):
    """경기 하나의 SSE 스트림. 연결이 끊길 때까지 이벤트를 계속 보낸다(비동기 제너레이터)."""
    if not isinstance(request, ASGIRequest):  # WSGI 서버라면 스트림마다 스레드 하나를 영원히 붙잡게 된다
        return JsonResponse({"detail": "the live stream is served by the django-sse service (ASGI)"}, status=503)
    match = await sync_to_async(_find_match)(key)
    q = hub.subscribe(key)  # 스냅숏보다 먼저 구독: 스냅숏을 읽는 사이에 온 갱신을 놓치지 않게

    async def events():
        """보낼 이벤트들: 재연결 간격 → 스냅숏 → (갱신 또는 15초마다 ping) 반복."""
        try:
            yield "retry: 3000\n\n"  # 끊기면 3초 뒤 다시 연결하라고 브라우저에 알림
            snap = await sync_to_async(queries.snapshot)(match)
            yield sse("snapshot", json.dumps(snap, ensure_ascii=False, default=str))
            while True:
                try:
                    value = await asyncio.wait_for(q.get(), HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse("update", value)  # 이미 한 줄짜리 JSON(live.metrics 메시지)이라 그대로 보낸다
        finally:
            hub.unsubscribe(key, q)  # 브라우저가 떠나면 구독 해제

    response = StreamingHttpResponse(events(), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # Nginx: 이벤트를 모아 두지 말고 바로 넘기라는 표시
    return response
