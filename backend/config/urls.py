from django.contrib import admin
from django.urls import path, re_path

from league import views as league
from matches import views as matches
from ops.views import health

MATCH_KEY = r"(?P<key>\d{3}-\d+)"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/seasons", league.SeasonListView.as_view(), name="seasons"),
    path("api/teams", league.TeamListView.as_view(), name="teams"),
    path("api/teams/<str:code>", league.TeamDetailView.as_view(), name="team-detail"),
    path("api/players/<str:code>", league.PlayerDetailView.as_view(), name="player-detail"),
    path("api/standings", league.StandingsView.as_view(), name="standings"),
    path("api/standings/players", league.PlayerRankingView.as_view(), name="player-ranking"),
    path("api/matches", matches.MatchListView.as_view(), name="matches"),
    re_path(rf"^api/matches/{MATCH_KEY}$", matches.MatchDetailView.as_view(), name="match-detail"),
    re_path(rf"^api/matches/{MATCH_KEY}/rallies$", matches.RallyView.as_view(), name="match-rallies"),
]
