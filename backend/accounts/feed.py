"""The "my teams and players" block at the top of the home page (WEB-12)."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from analytics.form import player_form
from league import queries as q
from league.standings import team_standings
from matches.models import Match, PlayerMatchStat
from matches.serializers import MatchListSerializer, match_key
from matches.views import base_matches


def _team_card(team, season, standings):
    if team.gender not in standings:
        teams, rows = q.standings_input(season, team.gender)
        standings[team.gender] = {r["team"]["code"]: r for r in team_standings(teams, rows)}
    record = standings[team.gender].get(team.code)
    mine = base_matches().filter(Q(home_team=team) | Q(away_team=team))
    last = mine.filter(status__in=q.FINISHED).order_by("-scheduled_at").first()
    nxt = (mine.filter(status__in=[Match.Status.SCHEDULED, Match.Status.LIVE],
                       scheduled_at__gte=timezone.now() - timedelta(hours=3))
           .order_by("scheduled_at").first())
    return {
        "team": {"code": team.code, "name": team.name, "gender": team.gender},
        "record": {k: record[k] for k in ("rank", "games", "wins", "losses", "points", "form")} if record else None,
        "last": MatchListSerializer(last).data if last else None,
        "next": MatchListSerializer(nxt).data if nxt else None,
    }


def _player_card(player, season):
    rows = list(PlayerMatchStat.objects.filter(player=player, match__competition__season=season,
                                               match__status__in=q.FINISHED)
                .exclude(played_sets="00000")
                .select_related("team", "match__home_team", "match__away_team", "match__competition__season")
                .order_by("match__scheduled_at"))
    if not rows:
        team = (PlayerMatchStat.objects.filter(player=player).select_related("team")
                .order_by("-match__scheduled_at").first())
        return {"player": {"code": player.code, "name": player.name, "position": player.position},
                "team": {"code": team.team.code, "name": team.team.name} if team else None,
                "season": None, "last": None, "form": None}
    log = []
    for s in rows:
        box = {f: getattr(s, f) for f in q.BOX_FIELDS}
        log.append({"match": match_key(s.match), "date": s.match.scheduled_at, "sets": s.played_sets.count("1"),
                    "points": s.points, **box})
    s = rows[-1]
    m = s.match
    home = m.home_team_id == s.team_id
    won, lost = (m.home_sets, m.away_sets) if home else (m.away_sets, m.home_sets)
    last = log[-1]
    totals = {f: sum(r[f] for r in log) for f in q.BOX_FIELDS}
    totals["sets_played"] = sum(r["sets"] for r in log)
    form = player_form(log)
    return {
        "player": {"code": player.code, "name": player.name, "position": player.position},
        "team": {"code": s.team.code, "name": s.team.name},
        "season": {"games": len(log), "sets_played": totals["sets_played"], "points": sum(r["points"] for r in log),
                   **_rates(totals, totals["sets_played"], MIN_SEASON)},
        "last": {"match": last["match"], "date": last["date"],
                 "opponent": (m.away_team if home else m.home_team).name,
                 "result": "W" if won > lost else "L", "score": f"{won}:{lost}",
                 "points": last["points"], "sets": last["sets"], **_rates(last, last["sets"], MIN_GAME)},
        "form": form["overall"] if form else None,
    }


# attempts needed before a rate is shown (an opposite's two receptions say nothing about receiving)
MIN_GAME, MIN_SEASON = 5, 15


def _rates(box, sets, least):
    r = q.rates({**box, "sets_played": sets})
    return {"attack_pct": r["attack_pct"] if box["attack_attempts"] >= least else None,
            "receive_efficiency": r["receive_efficiency"] if box["receive_attempts"] >= least else None}


def favorite_feed(user):
    """Cards for each followed team (record, last and next match) and player (last game, season so far)."""
    season = q.resolve_season(None)
    standings = {}
    teams = [f.team for f in user.favorite_teams.select_related("team").order_by("created_at")]
    players = [f.player for f in user.favorite_players.select_related("player").order_by("created_at")]
    return {"season": {"code": season.code, "years": season.years},
            "teams": [_team_card(t, season, standings) for t in teams],
            "players": [_player_card(p, season) for p in players]}
