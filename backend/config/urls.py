"""URL 라우팅: 모든 API 주소와 담당 뷰의 목록(설계서의 API 표와 같다).

  /admin/          Django 관리자(데이터 운영, 모델 관리 등)
  /api/...         Vue 화면이 부르는 REST API. 응답은 모두 JSON(DRF)
  /api/live/{경기}/stream  실시간 경기 SSE 스트림(django-sse 서비스가 처리)
경기 주소는 "{시즌}-{경기번호}" 형식(예: 023-6)이라 정규식(MATCH_KEY)으로 받는다.
"""
from django.contrib import admin
from django.urls import path, re_path

from accounts import views as accounts
from analytics import views as analytics
from articles import views as articles
from live import views as live
from league import views as league
from matches import views as matches
from ops.views import health

MATCH_KEY = r"(?P<key>\d{3}-\d+)"  # 경기 키: 시즌 코드 3자리 - 경기 번호

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/auth/signup", accounts.SignupView.as_view(), name="signup"),
    path("api/auth/login", accounts.LoginView.as_view(), name="login"),
    path("api/auth/logout", accounts.LogoutView.as_view(), name="logout"),
    path("api/me", accounts.MeView.as_view(), name="me"),
    path("api/me/feed", accounts.FeedView.as_view(), name="me-feed"),
    path("api/me/favorites", accounts.FavoriteListView.as_view(), name="favorites"),
    path("api/me/favorites/<str:kind>/<str:code>", accounts.FavoriteDetailView.as_view(), name="favorite-detail"),
    path("api/seasons", league.SeasonListView.as_view(), name="seasons"),
    path("api/teams", league.TeamListView.as_view(), name="teams"),
    path("api/teams/<str:code>", league.TeamDetailView.as_view(), name="team-detail"),
    path("api/players/<str:code>", league.PlayerDetailView.as_view(), name="player-detail"),
    path("api/standings", league.StandingsView.as_view(), name="standings"),
    path("api/standings/players", league.PlayerRankingView.as_view(), name="player-ranking"),
    path("api/matches", matches.MatchListView.as_view(), name="matches"),
    re_path(rf"^api/matches/{MATCH_KEY}$", matches.MatchDetailView.as_view(), name="match-detail"),
    re_path(rf"^api/matches/{MATCH_KEY}/rallies$", matches.RallyView.as_view(), name="match-rallies"),
    path("api/predictions", analytics.PredictionCheckView.as_view(), name="predictions"),
    path("api/ratings", analytics.RatingBoardView.as_view(), name="ratings"),
    path("api/live", live.LiveListView.as_view(), name="live"),
    re_path(rf"^api/live/{MATCH_KEY}$", live.LiveDetailView.as_view(), name="live-detail"),
    re_path(rf"^api/live/{MATCH_KEY}/stream$", live.stream, name="live-stream"),
    path("api/articles", articles.ArticleListView.as_view(), name="articles"),
    path("api/search", articles.SearchView.as_view(), name="search"),
    path("api/search/suggest", articles.SuggestView.as_view(), name="search-suggest"),
]
