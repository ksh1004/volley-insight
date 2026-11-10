"""분석 마트(Spark가 만든 요약 테이블)를 읽어 팀·선수·경기 API에 넣을 모양으로 만드는 함수들.

  팀:    Elo 추이, 사이드아웃/브레이크율, 강약점 레이더
  선수:  선수 유형·비슷한 선수, 포지션 내 백분위, 능력치 카드(시즌별·통산), 능력치 순위표
  경기:  최신 예측, 상대 전적, 경기 전 두 팀의 최근 폼(프리뷰)
"""
from collections import defaultdict

from django.db.models import Avg, F

from league.models import Season
from matches.serializers import match_key

from .models import PlayerCareerRating, PlayerRating, PlayerSeasonSummary, TeamRating, TeamSeasonSummary

REGULAR = "201"  # 정규리그 리그 코드


def _num(v):
    """DB의 Decimal을 JSON에 넣을 float로(None은 그대로)."""
    return float(v) if v is not None else None


def _rating_rows(team, rows):
    """Elo 행들을 차트용 점 목록으로: 날짜, 점수, 변화량, 경기 전 승리 확률, 상대, 결과(이 팀 기준), 포스트시즌 여부."""
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
    """팀의 Elo 행 쿼리셋(경기·시즌·두 팀을 함께 가져옴)."""
    return TeamRating.objects.filter(team=team).select_related("match__competition__season", "match__home_team",
                                                                "match__away_team")


def rating_history(team, season):
    """시즌의 경기마다 경기 후 Elo, 오래된 순(ANL-01). 팀 화면의 전력 지수 차트."""
    return _rating_rows(team, _ratings(team).filter(season=season).order_by("rated_on", "match__scheduled_at"))


def rating_tail(team, season, n=10):
    """`season` 바로 전 시즌의 마지막 `n`경기 Elo(오래된 순)와 그 시즌 연도. 시즌 초반 차트 앞에 흐리게 붙인다.
    전 시즌에 뛰지 않은 팀이면 빈 목록."""
    prev = Season.objects.filter(code__lt=season.code).order_by("-code").first()
    if prev is None:
        return {"season": None, "history": []}
    rows = list(_ratings(team).filter(season=prev).order_by("-rated_on", "-match__scheduled_at")[:n])
    return {"season": {"code": prev.code, "years": prev.years}, "history": _rating_rows(team, reversed(rows))}


def current_ratings(gender, season):
    """그 시즌에 뛴 모든 팀의 최신 Elo: {팀 코드: 점수}. 팀 차트에서 리그 안 위치를 보여 줄 때 쓴다.
    distinct("team_id")는 PostgreSQL의 DISTINCT ON: 팀마다 정렬상 첫 행(가장 최근)만 남긴다."""
    out = {}
    for r in (TeamRating.objects.filter(season=season, team__gender=gender).select_related("team")
              .order_by("team_id", "-rated_on", "-match__scheduled_at").distinct("team_id")):
        out[r.team.code] = _num(r.rating)
    return out


def team_play_rates(team, season):
    """팀의 사이드아웃·브레이크율과 상황별 효율, 그리고 리그 평균(ANL-11, 정규리그)."""
    base = TeamSeasonSummary.objects.filter(competition__season=season, competition__league_code=REGULAR,
                                            team__gender=team.gender)
    mine = base.filter(team=team).first()
    fields = ["sideout_rate", "break_rate", "attack_after_good_receive", "attack_after_poor_receive", "clutch_win_rate"]
    league = base.aggregate(**{f: Avg(f) for f in fields})
    team_vals = {f: _num(getattr(mine, f)) if mine else None for f in fields}
    team_vals["clutch_rallies"] = mine.clutch_rallies if mine else None
    return {"team": team_vals, "league": {f: _num(league[f]) for f in fields}}


# (키, 이름, 낮을수록 좋은가) — 팀 화면의 강약점 레이더 축들(ANL-02, WEB-06)
PROFILE_AXES = [
    ("attack_pct", "공격 성공률", False), ("receive_efficiency", "리시브 효율", False),
    ("blocks_per_set", "블로킹", False), ("serve_aces_per_set", "서브 에이스", False),
    ("digs_per_set", "디그", False), ("errors_per_set", "범실", True),
    ("sideout_rate", "사이드아웃", False), ("break_rate", "브레이크", False),
]


def team_profile(team, season, totals):
    """팀의 지표마다 리그 팀들 사이의 점수: 100 = 1위, 0 = 꼴찌, 50 = 가운데. 레이더 차트의 축 값이 된다.

    `totals`: 모든 팀의 시즌 박스스코어 합(league.queries.team_totals). 동점이면 같은 점수를 나눠 갖는다.
    점수 = (나보다 못한 팀 수 + 동점 팀 수의 절반) / (팀 수 - 1) × 100.
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
    """한 시즌의 선수 유형(K-means 그룹)과 가장 비슷한 선수들(ANL-06, WEB-07). 그 시즌 출전이 너무 적었으면 None.
    비슷한 선수는 코드만 저장돼 있어 이름·팀·시즌 연도를 한꺼번에 찾아 붙인다."""
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
        """저장된 코드들을 화면에 보여 줄 이름으로 바꾼다."""
        return [{"player": {"code": r["player"], "name": names.get(r["player"], r["player"])},
                 "season": {"code": r["season"], "years": years.get(r["season"], "")},
                 "team": {"code": r["team"], "name": teams.get(r["team"], r["team"])},
                 "type": r["type"], "similarity": r["similarity"]} for r in rows]

    t = s.type
    return {"type": {"label": t.label, "position": t.position, "traits": t.traits, "size": t.size, "k": t.k},
            "sets": s.sets, "provisional": s.provisional, "features": s.features, "z": s.z,
            "similar_season": resolve(s.similar_season), "similar_all": resolve(s.similar_all)}


def player_percentiles(player, season):
    """정규리그 지표와 같은 포지션 안 백분위(ANL-05). 없으면 None. 시즌 중 이적한 선수는 세트가 많은 팀의 행."""
    s = (PlayerSeasonSummary.objects.filter(player=player, competition__season=season, competition__league_code=REGULAR)
         .order_by("-sets").first())
    if not s:
        return None
    fields = ["attack_success_rate", "attack_efficiency", "receive_efficiency", "block_per_set", "serve_per_set",
              "dig_per_set", "set_per_set", "error_per_set"]
    return {"position": s.position, "qualified": s.qualified, "sets": s.sets,
            "values": {f: _num(getattr(s, f)) for f in fields}, "percentiles": s.percentiles or {}}


# ---------------------------------------------------------------- 경기 프리뷰 (WEB-08, ANL-03/04/10)
def latest_prediction(match):
    """경기의 최신 예측과, 그 예측을 만든 모델의 시험 성적·실제 적중 기록(예측을 얼마나 믿을지 판단할 근거)."""
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
    """이 경기 전에 두 팀이 치른 모든 경기의 상대 전적(ANL-04): 전체 수, 팀별 승수, 최근 `limit`경기."""
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
            mine = m.home_team_id == h  # 점수는 '이번 경기의 홈 팀' 입장에서 적는다
            recent.append({"match": match_key(m), "date": m.scheduled_at, "season": m.competition.season.years,
                           "score": f"{m.home_sets}:{m.away_sets}" if mine else f"{m.away_sets}:{m.home_sets}",
                           "winner": "home" if winner == h else "away"})
    return {"total": len(meetings), "wins": wins, "recent": recent}


def team_form(team, before, season, limit=5):
    """`before` 시각 이전 최근 `limit`경기 결과, 그때의 Elo, 시즌 누적 지표와 승패(프리뷰의 팀 비교)."""
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


# ---------------------------------------------------------------- 포지션별 능력치(선수 카드)
POSITION_NAMES = {"OH": "아웃사이드 히터", "OP": "아포짓", "MB": "미들 블로커", "S": "세터", "L": "리베로"}
CARD_KEYS = ("key", "label", "weight", "score", "stats", "note")  # 화면에 보내는 능력치 필드(z 등 내부 값은 뺌)


def _card(r):
    """시즌 카드와 통산 카드가 함께 쓰는 공통 부분: 포지션, 종합, 세트, 자격, 능력치, 추가 정보."""
    return {"position": r.position, "position_name": POSITION_NAMES.get(r.position, r.position), "overall": r.overall,
            "sets": r.sets, "qualified": r.qualified,
            "abilities": [{k: a.get(k) for k in CARD_KEYS} for a in r.abilities], "extra": r.extra}


def _season(s):
    """응답에 넣을 시즌 참조(코드, 연도)."""
    return {"code": s.code, "years": s.years}


def player_ratings(player):
    """선수의 모든 카드(하나도 없으면 None).
      seasons: 점수가 있는 모든 시즌(오래된 순). 같은 성별·포지션 자격 선수 중 순위, 같은 포지션의 직전 카드(비교용)
      careers: 포지션별 통산 카드(세트가 많은 포지션부터). 자격 시즌 3번 이상인 통산들 중 역대 순위
    순위는 '나보다 종합이 높은 사람 수 + 1'(동점은 같은 순위)."""
    rows = list(PlayerRating.objects.filter(player=player).select_related("season", "team").order_by("season__code"))
    careers = list(PlayerCareerRating.objects.filter(player=player)
                   .select_related("first_season", "last_season", "best_season").order_by("-sets", "position"))
    if not rows and not careers:
        return None
    peers = defaultdict(list)  # (시즌 id, 포지션) → 자격 선수들의 종합 점수. 모든 시즌을 쿼리 한 번으로
    if rows:
        for sid, pos, overall in (PlayerRating.objects
                                  .filter(gender=rows[0].gender, season_id__in={r.season_id for r in rows},
                                          position__in={r.position for r in rows}, qualified=True,
                                          overall__isnull=False)
                                  .values_list("season_id", "position", "overall")):
            peers[(sid, pos)].append(overall)
    seasons, last_at = [], {}  # last_at: 포지션 → 지금까지 본 그 포지션의 가장 최근 카드
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


def _board(qs, position, include_all, extra):
    """능력치 순위표의 행들(종합 높은 순). 순위 = 자격 있고 점수 있는 카드끼리의 경쟁 순위(1, 1, 3 식).
    나머지는 include_all일 때 순위 없이 함께 나온다. extra(카드)가 순위표마다 다른 필드(팀, 기간 등)를 더한다."""
    qs = qs.select_related("player").order_by(F("overall").desc(nulls_last=True), "player__name")
    cards = list(qs if include_all else qs.filter(qualified=True))
    rows, rank, prev_overall, n = [], None, None, 0
    for c in cards:
        ranked = c.qualified and c.overall is not None
        if ranked:
            n += 1
            if c.overall != prev_overall:  # 동점이면 그 점수 첫 선수의 순위를 그대로 쓴다
                rank, prev_overall = n, c.overall
        rows.append({"rank": rank if ranked else None, "player": {"code": c.player.code, "name": c.player.name},
                     "overall": c.overall, "sets": c.sets, "qualified": c.qualified,
                     "scores": {a["key"]: a.get("score") for a in c.abilities}, **extra(c)})
    # 표의 열 제목은 첫 카드에서: 한 포지션의 카드는 모두 같은 능력치를 같은 순서로 갖는다
    abilities = [{k: a[k] for k in ("key", "label", "weight")} for a in cards[0].abilities] if cards else []
    return {"position": position, "position_name": POSITION_NAMES.get(position, position), "abilities": abilities,
            "rows": rows}


def rating_board(season, gender, position, include_all=False):
    """한 시즌·성별·포지션의 선수 순위(종합 점수 순, 순위는 자격 선수끼리만)."""
    qs = PlayerRating.objects.filter(season=season, gender=gender, position=position).select_related("team")
    return _board(qs, position, include_all,
                  lambda r: {"team": {"code": r.team.code, "name": r.team.name}, "provisional": r.provisional})


def career_board(gender, position, include_all=False):
    """역대 순위표: 한 성별·포지션의 통산 카드를 종합 순으로(순위는 자격 시즌 3번 이상인 통산끼리만).
    선수마다 그 포지션으로 뛴 가장 최근 시즌의 팀을 함께 보여 준다."""
    qs = (PlayerCareerRating.objects.filter(gender=gender, position=position)
          .select_related("first_season", "last_season", "best_season"))
    teams = {}  # 선수 id → 그 포지션의 가장 최근 시즌 팀(최신순으로 돌며 처음 나온 것만 남김)
    for r in (PlayerRating.objects.filter(player__in=qs.values("player"), position=position)
              .select_related("team").order_by("player_id", "-season__code")):
        teams.setdefault(r.player_id, {"code": r.team.code, "name": r.team.name})
    return _board(qs, position, include_all, lambda c: {
        "team": teams.get(c.player_id), "seasons": c.seasons, "qualified_seasons": c.qualified_seasons,
        "provisional": not c.qualified, "first": _season(c.first_season), "last": _season(c.last_season),
        "best": {"season": _season(c.best_season), "overall": c.best_overall} if c.best_season else None})
