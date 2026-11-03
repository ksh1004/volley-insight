"""Reads of the analysis marts for the team and player APIs."""
from django.db.models import Avg

from matches.serializers import match_key

from .models import PlayerSeasonSummary, TeamRating, TeamSeasonSummary

REGULAR = "201"


def _num(v):
    return float(v) if v is not None else None


def rating_history(team, season):
    """Elo after each match of the season, oldest first (ANL-01)."""
    rows = (TeamRating.objects.filter(team=team, season=season)
            .select_related("match__competition__season", "match__home_team", "match__away_team")
            .order_by("rated_on", "match__scheduled_at"))
    out = []
    for r in rows:
        m = r.match
        home = m.home_team_id == team.id
        opp = m.away_team if home else m.home_team
        won = (m.home_sets > m.away_sets) == home
        out.append({"date": r.rated_on, "rating": _num(r.rating), "change": _num(r.rating_change),
                    "win_prob": _num(r.win_prob), "match": match_key(m), "opponent": opp.name,
                    "result": "W" if won else "L", "score": f"{m.home_sets}:{m.away_sets}" if home else f"{m.away_sets}:{m.home_sets}",
                    "postseason": m.competition.league_code != REGULAR})
    return out


def current_ratings(gender, season):
    """Latest Elo of every team that played in the season: {team_code: rating}."""
    out = {}
    for r in (TeamRating.objects.filter(season=season, team__gender=gender).select_related("team")
              .order_by("team_id", "-rated_on", "-match__scheduled_at").distinct("team_id")):
        out[r.team.code] = _num(r.rating)
    return out


def team_play_rates(team, season):
    """Side-out / break rate of the team and the league average (ANL-11), regular season."""
    base = TeamSeasonSummary.objects.filter(competition__season=season, competition__league_code=REGULAR,
                                            team__gender=team.gender)
    mine = base.filter(team=team).first()
    league = base.aggregate(sideout=Avg("sideout_rate"), brk=Avg("break_rate"))
    return {"team": {"sideout_rate": _num(mine.sideout_rate) if mine else None,
                     "break_rate": _num(mine.break_rate) if mine else None},
            "league": {"sideout_rate": _num(league["sideout"]), "break_rate": _num(league["brk"])}}


def player_percentiles(player, season):
    """Indicators and percentile within position for the regular season (ANL-05). None if not available."""
    s = (PlayerSeasonSummary.objects.filter(player=player, competition__season=season, competition__league_code=REGULAR)
         .order_by("-sets").first())
    if not s:
        return None
    fields = ["attack_success_rate", "attack_efficiency", "receive_efficiency", "block_per_set", "serve_per_set",
              "dig_per_set", "set_per_set", "error_per_set"]
    return {"position": s.position, "qualified": s.qualified, "sets": s.sets,
            "values": {f: _num(getattr(s, f)) for f in fields}, "percentiles": s.percentiles or {}}
