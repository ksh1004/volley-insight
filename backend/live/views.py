"""Live APIs (design.md 1.11, WEB-04).

  GET /api/live                 games followed now (live or replayed) with their current state
  GET /api/live/{key}           one game's state, set indicators and every point so far
  GET /api/live/{key}/stream    Server-Sent Events: `snapshot` once, then an `update` per live.metrics message
                                (served by the django-sse service under Uvicorn; see compose.yaml)
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

HEARTBEAT_SEC = 15  # keeps proxies from closing an idle stream


class LiveListView(APIView):
    def get(self, request):
        return Response({"results": queries.current(), "source": SOURCE})


class LiveDetailView(APIView):
    def get(self, request, key):
        return Response({**queries.snapshot(get_match(key)), "source": SOURCE})


def _find_match(key):
    m = re.fullmatch(r"(\d{3})-(\d+)", key)
    try:
        return base_matches().get(competition__season__code=m[1], game_no=int(m[2]))
    except Match.DoesNotExist:
        raise Http404("no such match")


def sse(event, data: str):
    return f"event: {event}\ndata: {data}\n\n"


async def stream(request, key):
    if not isinstance(request, ASGIRequest):  # the WSGI dev server would hold a thread per stream forever
        return JsonResponse({"detail": "the live stream is served by the django-sse service (ASGI)"}, status=503)
    match = await sync_to_async(_find_match)(key)
    q = hub.subscribe(key)  # before the snapshot, so no update falls between the two

    async def events():
        try:
            yield "retry: 3000\n\n"
            snap = await sync_to_async(queries.snapshot)(match)
            yield sse("snapshot", json.dumps(snap, ensure_ascii=False, default=str))
            while True:
                try:
                    value = await asyncio.wait_for(q.get(), HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse("update", value)  # already one line of JSON (live.metrics message)
        finally:
            hub.unsubscribe(key, q)

    response = StreamingHttpResponse(events(), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # Nginx: pass events through without buffering
    return response
