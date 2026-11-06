"""Article list and search APIs (WEB-09, WEB-10, design.md 1.11).

  GET /api/articles?team=&player=&page=&page_size=   newest first, from PostgreSQL
  GET /api/search?q=&page=                           teams, players and articles, from Elasticsearch
  GET /api/search/suggest?q=                         up to 8 teams/players for the header autocomplete
Articles link to the publisher's site; only title, summary and publisher are shown (NFR-06, WEB-14).
"""
from django.db.models import Prefetch
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from league.models import Player, Team

from . import search
from .models import Article

MAX_QUERY = 50


class ArticlePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


def article_json(a: Article) -> dict:
    return {"url": a.url, "title": a.title, "summary": a.summary, "publisher": a.publisher,
            "published_at": a.published_at, "source": a.source,
            "teams": [{"code": t.code, "name": t.name} for t in a.teams.all()],
            "players": [{"code": p.code, "name": p.name} for p in a.players.all()]}


class ArticleListView(APIView):
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
    q = " ".join((request.query_params.get("q") or "").split())
    if not q:
        raise ValidationError({"q": "검색어를 입력해 주세요"})
    return q[:MAX_QUERY]


UNAVAILABLE = {"detail": "검색 서비스에 연결할 수 없어요. 잠시 후 다시 시도해 주세요."}


class SearchView(APIView):
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
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()[:MAX_QUERY]
        if not q:
            return Response({"results": []})
        try:
            return Response({"q": q, "results": search.suggest(q)})
        except search.SearchUnavailable:
            return Response(UNAVAILABLE, status=503)
