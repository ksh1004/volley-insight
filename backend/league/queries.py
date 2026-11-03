"""Database reads shared by the league and matches APIs."""
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Length, Replace
from rest_framework.exceptions import NotFound, ValidationError

from matches.models import Match, PlayerMatchStat, TeamMatchStat

from .models import Season

FINISHED = [Match.Status.FINISHED, Match.Status.CONFIRMED]
REGULAR = "201"
BOX_FIELDS = ["attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_attempts", "serve_aces",
              "serve_errors", "block_attempts", "block_success", "block_effective", "block_assist", "receive_attempts",
              "receive_excellent", "receive_errors", "dig_attempts", "dig_success", "set_attempts", "set_success",
              "errors", "warnings"]
# number of "1"s in played_sets ("11100" -> 3)
SETS_PLAYED = Length("played_sets") - Length(Replace("played_sets", Value("1"), Value("")))


def resolve_season(code: str | None) -> Season:
    """The requested season, or the latest season that has matches."""
    if code:
        season = Season.objects.filter(code=code).first()
        if not season:
            raise NotFound(f"season {code} not found")
        return season
    season = Season.objects.filter(competitions__matches__isnull=False).order_by("-code").first()
    if not season:
        raise NotFound("no season has been loaded yet")
    return season


def gender_param(value: str | None, required=False):
    if value in (None, "") and not required:
        return None
    if value not in ("M", "F"):
        raise ValidationError({"gender": "M or F"})
    return value


def competition_matches(season, gender, league=REGULAR):
    qs = Match.objects.filter(competition__season=season, competition__league_code=league)
    return qs.filter(gender=gender) if gender else qs


def standings_input(season, gender, league=REGULAR):
    """(teams, finished match rows) for league.standings.team_standings."""
    matches = competition_matches(season, gender, league).select_related("home_team", "away_team")
    teams = {}
    for m in matches:
        teams[m.home_team.code] = m.home_team.name
        teams[m.away_team.code] = m.away_team.name
    rows = list(matches.filter(status__in=FINISHED).order_by("scheduled_at")
                .annotate(home_points=Sum("sets__home_points"), away_points=Sum("sets__away_points"))
                .values("home_sets", "away_sets", "home_points", "away_points",
                        home=F("home_team__code"), away=F("away_team__code")))
    return sorted(teams.items()), rows


def team_sets_played(season, gender, league=REGULAR) -> dict:
    out = {}
    for m in competition_matches(season, gender, league).filter(status__in=FINISHED).values(
            "home_sets", "away_sets", home=F("home_team__code"), away=F("away_team__code")):
        sets = m["home_sets"] + m["away_sets"]
        out[m["home"]] = out.get(m["home"], 0) + sets
        out[m["away"]] = out.get(m["away"], 0) + sets
    return out


def player_totals(season, gender, league=REGULAR, **filters):
    """Season totals per (player, team) for finished matches."""
    qs = PlayerMatchStat.objects.filter(match__competition__season=season, match__competition__league_code=league,
                                        match__status__in=FINISHED, **filters)
    if gender:
        qs = qs.filter(match__gender=gender)
    return list(qs.values(code=F("player__code"), name=F("player__name"), team_code=F("team__code"),
                          team_name=F("team__name"))
                .annotate(games=Count("id", filter=~Q(played_sets="00000")), sets_played=Sum(SETS_PLAYED),
                          points=Sum("points"), **{f: Sum(f) for f in BOX_FIELDS}))


def team_totals(season, gender, league=REGULAR):
    """Season totals per team (box score sums + sets played) for finished matches."""
    rows = (TeamMatchStat.objects.filter(match__competition__season=season, match__competition__league_code=league,
                                         match__gender=gender, match__status__in=FINISHED)
            .values(code=F("team__code"), name=F("team__name"))
            .annotate(games=Count("id"), points=Sum("points"), opponent_errors=Sum("opponent_errors"),
                      **{f: Sum(f) for f in BOX_FIELDS}))
    sets = team_sets_played(season, gender, league)
    return [{**r, "sets_played": sets.get(r["code"], 0)} for r in rows]


def rates(t: dict) -> dict:
    """Comparable per-set and percentage indicators from box-score totals."""
    s = t.get("sets_played") or 0

    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    def per_set(a):
        return round(a / s, 2) if s else None

    return {
        "attack_pct": pct(t["attack_success"], t["attack_attempts"]),
        "attack_efficiency": pct(t["attack_success"] - t["attack_blocked"] - t["attack_errors"], t["attack_attempts"]),
        "serve_aces_per_set": per_set(t["serve_aces"]),
        "serve_errors_per_set": per_set(t["serve_errors"]),
        "blocks_per_set": per_set(t["block_success"]),
        "receive_efficiency": pct(t["receive_excellent"] - t["receive_errors"], t["receive_attempts"]),
        "digs_per_set": per_set(t["dig_success"]),
        "sets_per_set": per_set(t["set_success"]),
        "errors_per_set": per_set(t["errors"]),
    }
