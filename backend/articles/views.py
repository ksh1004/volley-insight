"""기사 목록과 검색 API(WEB-09, WEB-10, 설계서 1.11).

  GET /api/articles?team=&player=&page=&page_size=   최신 기사부터, PostgreSQL에서
  GET /api/search?q=&page=                           팀·선수·기사 통합 검색, Elasticsearch에서
  GET /api/search/suggest?q=                         머리글 자동완성용 팀·선수 최대 8개
기사는 언론사 사이트로 연결만 하고, 제목·요약·언론사만 보여 준다(NFR-06, WEB-14).
"""
from django.db.models import Prefetch
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from league.models import Player, Team

from . import search
from .models import Article

MAX_QUERY = 50  # 검색어 최대 길이(아주 긴 입력으로 검색 엔진에 부담 주지 않게)


class ArticlePagination(PageNumberPagination):
    """기사 목록 페이지 나누기: 기본 20개, 최대 50개."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


def article_json(a: Article) -> dict:
    """기사 하나의 응답 모양(검색 결과와 같은 모양으로 맞춤)."""
    return {"url": a.url, "title": a.title, "summary": a.summary, "publisher": a.publisher,
            "published_at": a.published_at, "source": a.source,
            "teams": [{"code": t.code, "name": t.name} for t in a.teams.all()],
            "players": [{"code": p.code, "name": p.name} for p in a.players.all()]}


class ArticleListView(APIView):
    """GET /api/articles: 기사 목록. team/player를 주면 그 팀·선수 태그가 붙은 기사만."""

    def get(self, request):
        qs = Article.objects.prefetch_related(Prefetch("teams", queryset=Team.objects.order_by("name")),
                                              Prefetch("players", queryset=Player.objects.order_by("name")))
        if team := request.query_params.get("team"):
            qs = qs.filter(teams__code=team)
        if player := request.query_params.get("player"):
            qs = qs.filter(players__code=player)
        qs = qs.order_by("-published_at", "-id")
        paginator = ArticlePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([article_json(a) for a in page])


def query_param(request) -> str:
    """검색어 파라미터: 공백을 하나로 줄이고 최대 길이로 자른다. 비어 있으면 400."""
    q = " ".join((request.query_params.get("q") or "").split())
    if not q:
        raise ValidationError({"q": "검색어를 입력해 주세요"})
    return q[:MAX_QUERY]


# 검색 엔진(Elasticsearch)이 응답하지 않을 때 보내는 안내(503)
UNAVAILABLE = {"detail": "검색 서비스에 연결할 수 없어요. 잠시 후 다시 시도해 주세요."}


class SearchView(APIView):
    """GET /api/search: 통합 검색. 팀·선수는 첫 페이지에만, 기사는 페이지마다 10개씩."""

    def get(self, request):
        q = query_param(request)
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except ValueError:
            raise ValidationError({"page": "number"})
        try:
            entities = search.search_entities(q) if page == 1 else {"team": [], "player": []}
            articles = search.search_articles(q, page=page)
        except search.SearchUnavailable:
            return Response(UNAVAILABLE, status=503)
        return Response({"q": q, "teams": entities["team"], "players": entities["player"], "articles": articles})


class SuggestView(APIView):
    """GET /api/search/suggest: 글자를 칠 때마다 부르는 자동완성. 검색어가 비면 빈 목록."""

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()[:MAX_QUERY]
        if not q:
            return Response({"results": []})
        try:
            return Response({"q": q, "results": search.suggest(q)})
        except search.SearchUnavailable:
            return Response(UNAVAILABLE, status=503)
