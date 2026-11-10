"""기사 목록 API(PostgreSQL)와 검색 API 테스트. 검색은 Elasticsearch 대신 mock으로 바꿔 끼운다.

  docker compose exec -T django-api python manage.py test articles

확인하는 것: 최신순 정렬과 팀·선수 태그, 팀·선수 필터, 검색어 검사, 검색 결과 묶기,
Elasticsearch가 죽었을 때 503, 연결 오류 뒤 고장 난 클라이언트를 새로 만드는지.
"""
from datetime import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from league.models import Player, Team

from . import search
from .models import Article, ArticlePlayer, ArticleTeam

KST = timezone.get_fixed_timezone(9 * 60)  # 한국 시각(UTC+9)


class ArticleApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        """기사 2건: a는 IBK기업은행·현대건설·빅토리아 태그, b(더 최신)는 현대건설 태그만."""
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
        """최신 기사부터 나오고, 각 기사에 팀·선수 태그가 붙는다."""
        r = self.client.get("/api/articles").json()
        assert r["count"] == 2
        assert [x["publisher"] for x in r["results"]] == ["연합뉴스", "뉴시스"]
        assert r["results"][1]["teams"] == [{"code": "2006", "name": "IBK기업은행"}, {"code": "2001", "name": "현대건설"}]
        assert r["results"][1]["players"] == [{"code": "p1", "name": "빅토리아"}]

    def test_filter_by_team_and_player(self):
        """?team=, ?player=로 그 팀·선수가 태그된 기사만 거른다."""
        assert self.client.get("/api/articles?team=2001").json()["count"] == 2
        assert self.client.get("/api/articles?team=2006").json()["count"] == 1
        assert self.client.get("/api/articles?player=p1").json()["count"] == 1

    def test_search_requires_query(self):
        """빈 검색어는 400, 자동완성은 검색어가 없으면 빈 결과."""
        assert self.client.get("/api/search?q=%20").status_code == 400
        assert self.client.get("/api/search/suggest").json() == {"results": []}

    def test_search_groups_results(self):
        """검색 결과를 팀·선수·기사로 나눠 돌려준다."""
        docs = [{"type": "team", "code": "2006", "name": "IBK기업은행"}, {"type": "player", "code": "p1", "name": "빅토리아"}]
        with mock.patch.object(search, "suggest", return_value=docs), \
             mock.patch.object(search, "search_articles", return_value={"total": 0, "page": 1, "results": []}):
            r = self.client.get("/api/search?q=기업").json()
        assert r["teams"][0]["code"] == "2006" and r["players"][0]["code"] == "p1"

    def test_search_down_returns_503(self):
        """Elasticsearch를 쓸 수 없으면 500이 아니라 503(잠시 이용 불가)으로 답한다."""
        with mock.patch.object(search, "suggest", side_effect=search.SearchUnavailable("down")):
            assert self.client.get("/api/search/suggest?q=김").status_code == 503
            assert self.client.get("/api/search?q=김").status_code == 503

    def test_client_stuck_after_a_connection_error_is_replaced(self):
        """연결 오류가 난 클라이언트는 버리고 다음 요청에서 새로 만든다(고장 난 연결에 계속 묶이지 않게)."""
        from elastic_transport import ConnectionError as Down

        search.client.cache_clear()
        broken, fresh = mock.Mock(), mock.Mock()
        broken.search.side_effect = Down("timed out")
        fresh.search.return_value = {"hits": {"hits": []}}
        try:
            with mock.patch("elasticsearch.Elasticsearch", side_effect=[broken, fresh]):
                with self.assertRaises(search.SearchUnavailable):
                    search.suggest("김")
                assert search.suggest("김") == []  # 고장 난 것이 아닌 새 클라이언트로 성공
        finally:
            search.client.cache_clear()
