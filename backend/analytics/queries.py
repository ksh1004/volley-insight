"""Reads of the analysis marts for the team and player APIs."""
from collections import defaultdict

from django.db.models import Avg, F

from league.models import Season
from matches.serializers import match_key

from .models import PlayerCareerRating, PlayerRating, PlayerSeasonSummary, TeamRating, TeamSeasonSummary

REGULAR = "201"


def _num(v):
    return float(v) if v is not None else None


def _rating_rows(team, rows):
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


def _ratings(team):
    return TeamRating.objects.filter(team=team).select_related("match__competition__season", "match__home_team",
                                                                "match__away_team")


def rating_history(team, season):
    """Elo after each match of the season, oldest first (ANL-01)."""
    return _rating_rows(team, _ratings(team).filter(season=season).order_by("rated_on", "match__scheduled_at"))


def rating_tail(team, season, n=10):
    """The team's last `n` rated matches of the season before `season` (oldest first) and that season's years.
    Shown faded before an early-season chart; empty when the team did not play the season before."""
    prev = Season.objects.filter(code__lt=season.code).order_by("-code").first()
    if prev is None:
        return {"season": None, "history": []}
    rows = list(_ratings(team).filter(season=prev).order_by("-rated_on", "-match__scheduled_at")[:n])
    return {"season": {"code": prev.code, "years": prev.years}, "history": _rating_rows(team, reversed(rows))}


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
    fields = ["sideout_rate", "break_rate", "attack_after_good_receive", "attack_after_poor_receive", "clutch_win_rate"]
    league = base.aggregate(**{f: Avg(f) for f in fields})
    team_vals = {f: _num(getattr(mine, f)) if mine else None for f in fields}
    team_vals["clutch_rallies"] = mine.clutch_rallies if mine else None
    return {"team": team_vals, "league": {f: _num(league[f]) for f in fields}}


# (key, label, lower is better) — the strength/weakness radar on the team page (ANL-02, WEB-06)
PROFILE_AXES = [
    ("attack_pct", "공격 성공률", False), ("receive_efficiency", "리시브 효율", False),
    ("blocks_per_set", "블로킹", False), ("serve_aces_per_set", "서브 에이스", False),
    ("digs_per_set", "디그", False), ("errors_per_set", "범실", True),
    ("sideout_rate", "사이드아웃", False), ("break_rate", "브레이크", False),
]


def team_profile(team, season, totals):
    """Each indicator of the team as a score among the league's teams: 100 = best, 0 = worst, 50 = middle.

    `totals` are the season box-score sums of every team (league.queries.team_totals). Ties share the score.
    """
    from league.queries import rates

    values = {t["code"]: rates(t) for t in totals}
    for s in TeamSeasonSummary.objects.filter(competition__season=season, competition__league_code=REGULAR,
                                              team__gender=team.gender).select_related("team"):
        if s.team.code in values:
            values[s.team.code].update(sideout_rate=_num(s.sideout_rate), break_rate=_num(s.break_rate))
    games = next((t["games"] for t in totals if t["code"] == team.code), 0)
    axes = []
    for key, label, lower in PROFILE_AXES:
        xs = [v.get(key) for v in values.values() if v.get(key) is not None]
        mine = values.get(team.code, {}).get(key)
        axis = {"key": key, "label": label, "lower_is_better": lower, "value": mine,
                "league": round(sum(xs) / len(xs), 2) if xs else None, "teams": len(xs), "rank": None, "score": None}
        if mine is not None and len(xs) >= 2:
            better = sum(1 for x in xs if (x < mine if lower else x > mine))
            ties = sum(1 for x in xs if x == mine) - 1
            axis["rank"] = better + 1
            axis["score"] = round(100 * (len(xs) - 1 - better - ties / 2) / (len(xs) - 1))
        axes.append(axis)
    return {"games": games, "axes": axes}


def player_style(player, season):
    """The player's type (K-means group) and most similar players for one season (ANL-06, WEB-07). None if the
    player had too few sets that season."""
    from league.models import Player, Team

    from .models import PlayerStyle

    s = PlayerStyle.objects.filter(player=player, season=season).select_related("type").first()
    if not s:
        return None
    refs = s.similar_season + s.similar_all
    names = dict(Player.objects.filter(code__in={r["player"] for r in refs}).values_list("code", "name"))
    teams = dict(Team.objects.filter(code__in={r["team"] for r in refs}).values_list("code", "name"))
    years = dict(Season.objects.filter(code__in={r["season"] for r in refs}).values_list("code", "years"))

    def resolve(rows):
        return [{"player": {"code": r["player"], "name": names.get(r["player"], r["player"])},
                 "season": {"code": r["season"], "years": years.get(r["season"], "")},
                 "team": {"code": r["team"], "name": teams.get(r["team"], r["team"])},
                 "type": r["type"], "similarity": r["similarity"]} for r in rows]

    t = s.type
    return {"type": {"label": t.label, "position": t.position, "traits": t.traits, "size": t.size, "k": t.k},
            "sets": s.sets, "provisional": s.provisional, "features": s.features, "z": s.z,
            "similar_season": resolve(s.similar_season), "similar_all": resolve(s.similar_all)}


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


# ---------------------------------------------------------------- match preview (WEB-08, ANL-03/04/10)
def latest_prediction(match):
    from .models import MatchPrediction

    p = (MatchPrediction.objects.filter(match=match).select_related("model_version")
         .order_by("-predicted_at").first())
    if not p:
        return None
    mv = p.model_version
    scored = MatchPrediction.objects.filter(model_version=mv, is_correct__isnull=False)
    hits = scored.filter(is_correct=True).count()
    return {
        "home_win_prob": _num(p.home_win_prob), "set_score_probs": p.set_score_probs,
        "pred_sets": [p.pred_home_sets, p.pred_away_sets], "predicted_at": p.predicted_at, "is_correct": p.is_correct,
        "model": {"name": mv.name, "version": mv.version, "test_season": mv.test_season, "train_seasons": mv.train_seasons,
                  "test_season_years": Season.objects.filter(code=mv.test_season).values_list("years", flat=True).first(),
                  "test_accuracy": mv.metrics.get("test_accuracy"), "elo_test_accuracy": mv.metrics.get("elo_test_accuracy"),
                  "test_log_loss": mv.metrics.get("test_log_loss"), "elo_test_log_loss": mv.metrics.get("elo_test_log_loss"),
                  "live_scored": scored.count(), "live_hits": hits},
    }


def head_to_head(match, limit=5):
    """All finished meetings of the two teams before this match (ANL-04)."""
    from django.db.models import Q

    from matches.models import Match

    h, a = match.home_team_id, match.away_team_id
    meetings = (Match.objects.filter(Q(home_team_id=h, away_team_id=a) | Q(home_team_id=a, away_team_id=h),
                                     status__in=["FINISHED", "CONFIRMED"], scheduled_at__lt=match.scheduled_at)
                .select_related("competition__season").order_by("-scheduled_at"))
    wins = {"home": 0, "away": 0}
    recent = []
    for m in meetings:
        winner = m.home_team_id if m.home_sets > m.away_sets else m.away_team_id
        wins["home" if winner == h else "away"] += 1
        if len(recent) < limit:
            mine = m.home_team_id == h  # score from this match's home team point of view
            recent.append({"match": match_key(m), "date": m.scheduled_at, "season": m.competition.season.years,
                           "score": f"{m.home_sets}:{m.away_sets}" if mine else f"{m.away_sets}:{m.home_sets}",
                           "winner": "home" if winner == h else "away"})
    return {"total": len(meetings), "wins": wins, "recent": recent}


def team_form(team, before, season, limit=5):
    """Last `limit` results before a date, the current Elo, and season-to-date indicators."""
    from django.db.models import Q

    from matches.models import Match

    games = (Match.objects.select_related("competition__season").filter(Q(home_team=team) | Q(away_team=team), status__in=["FINISHED", "CONFIRMED"],
                                  scheduled_at__lt=before).order_by("-scheduled_at")[:limit])
    form = []
    for m in games:
        home = m.home_team_id == team.id
        won = (m.home_sets > m.away_sets) == home
        form.append({"result": "W" if won else "L", "match": match_key(m),
                     "score": f"{m.home_sets}:{m.away_sets}" if home else f"{m.away_sets}:{m.home_sets}"})
    rating = (TeamRating.objects.filter(team=team, rated_on__lt=before.date()).order_by("-rated_on").first())
    summary = TeamSeasonSummary.objects.filter(team=team, competition__season=season, competition__league_code=REGULAR).first()
    fields = ["attack_success_rate", "receive_efficiency", "block_per_set", "serve_per_set", "dig_per_set",
              "error_per_set", "sideout_rate", "break_rate"]
    return {"form": form, "elo": _num(rating.rating) if rating else None,
            "season": {f: _num(getattr(summary, f)) for f in fields} if summary else None,
            "record": f"{summary.wins}승 {summary.losses}패" if summary else None}


# ---------------------------------------------------------------- position ratings (player cards)
POSITION_NAMES = {"OH": "아웃사이드 히터", "OP": "아포짓", "MB": "미들 블로커", "S": "세터", "L": "리베로"}
CARD_KEYS = ("key", "label", "weight", "score", "stats", "note")


def _card(r):
    return {"position": r.position, "position_name": POSITION_NAMES.get(r.position, r.position), "overall": r.overall,
            "sets": r.sets, "qualified": r.qualified,
            "abilities": [{k: a.get(k) for k in CARD_KEYS} for a in r.abilities], "extra": r.extra}


def _season(s):
    return {"code": s.code, "years": s.years}


def player_ratings(player):
    """All of the player's cards (None without any): every rated season, oldest first, with its rank among the
    qualified players of the same gender and position and the change from his latest earlier season at the same
    position; and a career card per position (most sets first) with the all-time rank among careers of 3+
    qualified seasons."""
    rows = list(PlayerRating.objects.filter(player=player).select_related("season", "team").order_by("season__code"))
    careers = list(PlayerCareerRating.objects.filter(player=player)
                   .select_related("first_season", "last_season", "best_season").order_by("-sets", "position"))
    if not rows and not careers:
        return None
    peers = defaultdict(list)  # (season id, position) -> qualified overalls, one query for all his seasons
    if rows:
        for sid, pos, overall in (PlayerRating.objects
                                  .filter(gender=rows[0].gender, season_id__in={r.season_id for r in rows},
                                          position__in={r.position for r in rows}, qualified=True,
                                          overall__isnull=False)
                                  .values_list("season_id", "position", "overall")):
            peers[(sid, pos)].append(overall)
    seasons, last_at = [], {}  # position -> his latest card at it so far
    for r in rows:
        group = peers[(r.season_id, r.position)]
        prev = last_at.get(r.position)
        seasons.append({**_card(r), "season": _season(r.season), "team": {"code": r.team.code, "name": r.team.name},
                        "provisional": r.provisional, "peers": len(group),
                        "rank": sum(o > r.overall for o in group) + 1 if r.qualified and r.overall is not None else None,
                        "previous": None if prev is None else {
                            "season": _season(prev.season), "overall": prev.overall,
                            "abilities": {a["key"]: a.get("score") for a in prev.abilities}}})
        last_at[r.position] = r
    out = []
    for c in careers:
        ranked = PlayerCareerRating.objects.filter(gender=c.gender, position=c.position, qualified=True,
                                                   overall__isnull=False)
        out.append({**_card(c), "seasons": c.seasons, "qualified_seasons": c.qualified_seasons,
                    "first": _season(c.first_season), "last": _season(c.last_season),
                    "best": {"season": _season(c.best_season), "overall": c.best_overall} if c.best_season else None,
                    "peers": ranked.count(),
                    "rank": ranked.filter(overall__gt=c.overall).count() + 1
                    if c.qualified and c.overall is not None else None})
    return {"seasons": seasons, "careers": out}


def rating_board(season, gender, position, include_all=False):
    """Players of one gender and position ranked by overall (rank only among qualified players)."""
    qs = (PlayerRating.objects.filter(season=season, gender=gender, position=position)
          .select_related("player", "team").order_by(F("overall").desc(nulls_last=True), "player__name"))
    if not include_all:
        qs = qs.filter(qualified=True)
    rows, rank, prev_overall, n = [], None, None, 0
    for r in qs:
        if r.qualified and r.overall is not None:
            n += 1
            if r.overall != prev_overall:
                rank, prev_overall = n, r.overall
        rows.append({"rank": rank if r.qualified and r.overall is not None else None,
                     "player": {"code": r.player.code, "name": r.player.name},
                     "team": {"code": r.team.code, "name": r.team.name}, "overall": r.overall, "sets": r.sets,
                     "qualified": r.qualified, "provisional": r.provisional,
                     "scores": {a["key"]: a.get("score") for a in r.abilities}})
    first = qs.first()
    abilities = [{"key": a["key"], "label": a["label"], "weight": a["weight"]} for a in first.abilities] if first else []
    return {"position": position, "position_name": POSITION_NAMES.get(position, position), "abilities": abilities,
            "rows": rows}
