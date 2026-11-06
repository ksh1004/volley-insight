from django.contrib import admin
from django.urls import path, re_path

from accounts import views as accounts
from analytics import views as analytics
from articles import views as articles
from live import views as live
from league import views as league
from matches import views as matches
from ops.views import health

MATCH_KEY = r"(?P<key>\d{3}-\d+)"

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
    path("api/live", live.LiveListView.as_view(), name="live"),
    re_path(rf"^api/live/{MATCH_KEY}$", live.LiveDetailView.as_view(), name="live-detail"),
    re_path(rf"^api/live/{MATCH_KEY}/stream$", live.stream, name="live-stream"),
    path("api/articles", articles.ArticleListView.as_view(), name="articles"),
    path("api/search", articles.SearchView.as_view(), name="search"),
    path("api/search/suggest", articles.SuggestView.as_view(), name="search-suggest"),
]
