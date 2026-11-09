"""League APIs (design.md 1.11): seasons, standings, player rankings, team and player detail."""
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics import queries as aq
from analytics.form import player_form
from matches.models import Match, PlayerMatchStat
from matches.serializers import MatchListSerializer, match_key
from matches.views import SOURCE, base_matches

from . import queries as q
from .models import Player, Season, Team
from .standings import CATEGORIES, player_ranking, team_standings


def season_ref(s: Season):
    return {"code": s.code, "name": s.name, "years": s.years}


class SeasonListView(APIView):
    """GET /api/seasons: loaded seasons (newest first) and their competitions."""

    def get(self, request):
        seasons = (Season.objects.annotate(n=Count("competitions__matches")).filter(n__gt=0)
                   .prefetch_related("competitions").order_by("-code"))
        return Response([{**season_ref(s), "start_date": s.start_date, "end_date": s.end_date,
                          "competitions": [{"league_code": c.league_code, "type": c.type, "name": c.name}
                                           for c in sorted(s.competitions.all(), key=lambda c: c.league_code)]}
                         for s in seasons])


class TeamListView(APIView):
    """GET /api/teams?season=&gender=: teams that play in the season."""

    def get(self, request):
        season = q.resolve_season(request.query_params.get("season"))
        gender = q.gender_param(request.query_params.get("gender"))
        teams = Team.objects.filter(Q(home_matches__competition__season=season) | Q(away_matches__competition__season=season))
        if gender:
            teams = teams.filter(gender=gender)
        return Response([{"code": t.code, "name": t.name, "full_name": t.full_name, "gender": t.gender}
                         for t in teams.distinct().order_by("gender", "name")])


class StandingsView(APIView):
    """GET /api/standings?season=&gender=M|F&league=201"""

    def get(self, request):
        p = request.query_params
        season = q.resolve_season(p.get("season"))
        gender = q.gender_param(p.get("gender"), required=True)
        league = p.get("league", q.REGULAR)
        teams, matches = q.standings_input(season, gender, league)
        return Response({"season": season_ref(season), "gender": gender, "league_code": league,
                         "rule": "승점 → 승수 → 세트 득실률 → 점수 득실률 (3:0·3:1 승 3점, 3:2 승 2점, 2:3 패 1점)",
                         "rows": team_standings(teams, matches), "source": SOURCE})


class PlayerRankingView(APIView):
    """GET /api/standings/players?season=&gender=&category=points|attack|serve|block|dig|set|receive&limit=20"""

    def get(self, request):
        p = request.query_params
        season = q.resolve_season(p.get("season"))
        gender = q.gender_param(p.get("gender"), required=True)
        category = p.get("category", "points")
        if category not in CATEGORIES:
            raise ValidationError({"category": sorted(CATEGORIES)})
        limit = min(int(p.get("limit", 20)), 100)
        ranking = player_ranking(q.player_totals(season, gender), q.team_sets_played(season, gender), category, limit)
        return Response({"season": season_ref(season), "gender": gender, **ranking,
                         "qualification": "소속 팀 세트의 1/3 이상 출전 (공격 성공률은 팀 세트당 공격 시도 2회 이상, "
                                          "리시브 효율은 팀 세트당 리시브 1회 이상). 이 사이트의 기준이다.",
                         "source": SOURCE})


def team_form(team, season):
    """Players of the team whose last five games rose or fell clearly (ANL-08), strongest change first."""
    games = {}
    rows = (PlayerMatchStat.objects.filter(team=team, match__competition__season=season, match__status__in=q.FINISHED)
            .exclude(played_sets="00000").select_related("player", "match").order_by("match__scheduled_at"))
    for s in rows:
        games.setdefault(s.player, []).append({"match": match_key(s.match), "date": s.match.scheduled_at,
                                                "sets": s.played_sets.count("1"), "points": s.points,
                                                **{f: getattr(s, f) for f in q.BOX_FIELDS}})
    out = []
    for player, log in games.items():
        f = player_form(log)
        if not f:
            continue
        moved = [m for m in f["metrics"] if m["trend"] in ("up", "down")]
        if moved:
            out.append({"player": {"code": player.code, "name": player.name}, "overall": f["overall"],
                        "metrics": moved, "strength": max(abs(m["z"]) for m in moved)})
    return sorted(out, key=lambda r: -r["strength"])


class TeamDetailView(APIView):
    """GET /api/teams/{code}?season=: record, indicators vs league average, roster, recent and next matches (WEB-06)."""

    def get(self, request, code):
        team = get_object_or_404(Team, code=code)
        seasons = list(Season.objects.filter(Q(competitions__matches__home_team=team) | Q(competitions__matches__away_team=team))
                       .distinct().order_by("-code"))
        season = q.resolve_season(request.query_params.get("season") or (seasons[0].code if seasons else None))

        teams, rows = q.standings_input(season, team.gender)
        record = next((r for r in team_standings(teams, rows) if r["team"]["code"] == code), None)

        totals = q.team_totals(season, team.gender)
        mine = next((t for t in totals if t["code"] == code), None)
        league_sum = {f: sum(t[f] for t in totals) for f in q.BOX_FIELDS + ["sets_played"]}
        # league average per team-set: sum over teams / sum of their sets
        stats = {"team": q.rates(mine) if mine else None, "league": q.rates(league_sum) if totals else None}
        play = aq.team_play_rates(team, season)  # side-out / break from the marts
        if stats["team"]:
            stats["team"].update(play["team"])
            stats["league"].update(play["league"])

        roster = sorted(q.player_totals(season, team.gender, team__code=code), key=lambda r: -(r["points"] or 0))
        positions = dict(PlayerMatchStat.objects.filter(team=team, match__competition__season=season)
                         .exclude(position="").values_list("player__code", "position"))
        numbers = dict(PlayerMatchStat.objects.filter(team=team, match__competition__season=season)
                       .exclude(back_number=None).values_list("player__code", "back_number"))
        matches = base_matches().filter(Q(home_team=team) | Q(away_team=team), competition__season=season)
        recent = matches.filter(status__in=q.FINISHED).order_by("-scheduled_at")[:5]
        upcoming = matches.filter(status=Match.Status.SCHEDULED, scheduled_at__gte=timezone.now()).order_by("scheduled_at")[:3]
        history = aq.rating_history(team, season)
        return Response({
            "team": {"code": team.code, "name": team.name, "full_name": team.full_name, "gender": team.gender,
                     "home_arena": team.home_arena, "home_city": team.home_city, "is_active": team.is_active},
            "season": season_ref(season), "seasons": [season_ref(s) for s in seasons],
            "record": record, "stats": stats,
            "profile": aq.team_profile(team, season, totals),
            "form": team_form(team, season),
            "rating": {"history": history,
                       # early in a season the chart also shows the end of the season before, faded
                       "previous": aq.rating_tail(team, season) if len(history) < 10 else None,
                       "league": aq.current_ratings(team.gender, season)},
            "roster": [{"player": {"code": r["code"], "name": r["name"]}, "position": positions.get(r["code"], ""),
                        "back_number": numbers.get(r["code"]), "games": r["games"], "sets_played": r["sets_played"],
                        "points": r["points"], **q.rates(r)} for r in roster if r["games"]],
            "recent": MatchListSerializer(recent, many=True).data,
            "upcoming": MatchListSerializer(upcoming, many=True).data,
            "source": SOURCE,
        })


class PlayerDetailView(APIView):
    """GET /api/players/{code}?season=: profile, per-season totals, match log of one season (WEB-07)."""

    def get(self, request, code):
        player = get_object_or_404(Player, code=code)
        stats = PlayerMatchStat.objects.filter(player=player).exclude(played_sets="00000")
        by_season = (stats.filter(match__status__in=q.FINISHED, match__competition__league_code=q.REGULAR)
                     .values("match__competition__season__code", "match__competition__season__name", "team__code", "team__name")
                     .annotate(games=Count("id"), sets_played=Sum(q.SETS_PLAYED), points=Sum("points"),
                               **{f: Sum(f) for f in q.BOX_FIELDS})
                     .order_by("-match__competition__season__code"))
        career = [{"season": {"code": r["match__competition__season__code"], "name": r["match__competition__season__name"]},
                   "team": {"code": r["team__code"], "name": r["team__name"]},
                   "games": r["games"], "sets_played": r["sets_played"], "points": r["points"],
                   **{f: r[f] for f in q.BOX_FIELDS}, **q.rates(r)} for r in by_season]
        season_codes = sorted({c["season"]["code"] for c in career} |
                              set(stats.values_list("match__competition__season__code", flat=True)), reverse=True)
        season = q.resolve_season(request.query_params.get("season") or (season_codes[0] if season_codes else None))

        log = []
        for s in (stats.filter(match__competition__season=season).select_related(
                "match__competition__season", "match__home_team", "match__away_team", "team")
                .order_by("match__scheduled_at")):
            m = s.match
            home = m.home_team_id == s.team_id
            opp = m.away_team if home else m.home_team
            won_sets, lost_sets = (m.home_sets, m.away_sets) if home else (m.away_sets, m.home_sets)
            row = {f: getattr(s, f) for f in q.BOX_FIELDS}
            row["sets_played"] = s.played_sets.count("1")
            log.append({"match": match_key(m), "date": m.scheduled_at, "competition": m.competition.name,
                        "home": home, "opponent": {"code": opp.code, "name": opp.name},
                        "status": m.status,
                        "result": None if m.status not in q.FINISHED else ("W" if won_sets > lost_sets else "L"),
                        "score": f"{won_sets}:{lost_sets}", "points": s.points, "played_sets": s.played_sets,
                        **row, **q.rates(row)})
        latest = stats.select_related("team").order_by("-match__scheduled_at").first()
        return Response({
            "player": {"code": player.code, "name": player.name, "gender": player.gender, "position": player.position,
                       "height_cm": player.height_cm},
            "team": {"code": latest.team.code, "name": latest.team.name} if latest else None,
            "back_number": latest.back_number if latest else None,
            "season": season_ref(season), "seasons": season_codes,
            "career": career, "matches": log, "profile": aq.player_percentiles(player, season),
            "style": aq.player_style(player, season),
            "ratings": aq.player_ratings(player),
            "form": player_form([{"match": r["match"], "date": r["date"], "sets": r["sets_played"], "points": r["points"],
                                  **{f: r[f] for f in q.BOX_FIELDS}} for r in log if r["result"]]),
            "source": SOURCE,
        })
