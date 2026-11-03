from rest_framework import serializers

from .models import Match


def match_key(m: Match) -> str:
    """Stable public id: season code + KOVO game number, e.g. 022-3."""
    return f"{m.competition.season.code}-{m.game_no}"


def team_ref(team):
    return {"code": team.code, "name": team.name}


class MatchListSerializer(serializers.ModelSerializer):
    key = serializers.SerializerMethodField()
    season = serializers.CharField(source="competition.season.code")
    season_years = serializers.CharField(source="competition.season.years")
    league_code = serializers.CharField(source="competition.league_code")
    competition = serializers.CharField(source="competition.name")
    home = serializers.SerializerMethodField()
    away = serializers.SerializerMethodField()
    sets = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = ["key", "season", "season_years", "league_code", "competition", "round", "gender", "scheduled_at", "venue",
                  "broadcasters", "status", "home", "away", "home_sets", "away_sets", "sets"]

    def get_key(self, m):
        return match_key(m)

    def get_home(self, m):
        return team_ref(m.home_team)

    def get_away(self, m):
        return team_ref(m.away_team)

    def get_sets(self, m):
        return [[s.home_points, s.away_points] for s in m.sets.all()]
