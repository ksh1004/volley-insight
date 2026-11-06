"""Article list from PostgreSQL; search endpoints with Elasticsearch replaced by fakes."""
from datetime import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from league.models import Player, Team

from . import search
from .models import Article, ArticlePlayer, ArticleTeam

KST = timezone.get_fixed_timezone(9 * 60)


class ArticleApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ibk = Team.objects.create(code="2006", name="IBK기업은행", gender="F")
        hd = Team.objects.create(code="2001", name="현대건설", gender="F")
        vic = Player.objects.create(code="p1", name="빅토리아", gender="F")
        a = Article.objects.create(url="https://www.newsis.com/view/1", title="'빅토리아 21점' IBK기업은행 완파",
                                   summary="요약", publisher="뉴시스", source="RSS",
                                   published_at=datetime(2026, 11, 3, 21, 26, tzinfo=KST), collected_at=timezone.now())
        b = Article.objects.create(url="https://www.yna.co.kr/view/2", title="현대건설 소식", publisher="연합뉴스",
                                   source="RSS", published_at=datetime(2026, 11, 4, 9, 0, tzinfo=KST),
                                   collected_at=timezone.now())
        ArticleTeam.objects.create(article=a, team=ibk)
        ArticleTeam.objects.create(article=a, team=hd)
        ArticleTeam.objects.create(article=b, team=hd)
        ArticlePlayer.objects.create(article=a, player=vic)

    def test_list_newest_first_with_tags(self):
        r = self.client.get("/api/articles").json()
        assert r["count"] == 2
        assert [x["publisher"] for x in r["results"]] == ["연합뉴스", "뉴시스"]
        assert r["results"][1]["teams"] == [{"code": "2006", "name": "IBK기업은행"}, {"code": "2001", "name": "현대건설"}]
        assert r["results"][1]["players"] == [{"code": "p1", "name": "빅토리아"}]

    def test_filter_by_team_and_player(self):
        assert self.client.get("/api/articles?team=2001").json()["count"] == 2
        assert self.client.get("/api/articles?team=2006").json()["count"] == 1
        assert self.client.get("/api/articles?player=p1").json()["count"] == 1

    def test_search_requires_query(self):
        assert self.client.get("/api/search?q=%20").status_code == 400
        assert self.client.get("/api/search/suggest").json() == {"results": []}

    def test_search_groups_results(self):
        docs = [{"type": "team", "code": "2006", "name": "IBK기업은행"}, {"type": "player", "code": "p1", "name": "빅토리아"}]
        with mock.patch.object(search, "suggest", return_value=docs), \
             mock.patch.object(search, "search_articles", return_value={"total": 0, "page": 1, "results": []}):
            r = self.client.get("/api/search?q=기업").json()
        assert r["teams"][0]["code"] == "2006" and r["players"][0]["code"] == "p1"

    def test_search_down_returns_503(self):
        with mock.patch.object(search, "suggest", side_effect=search.SearchUnavailable("down")):
            assert self.client.get("/api/search/suggest?q=김").status_code == 503
            assert self.client.get("/api/search?q=김").status_code == 503
