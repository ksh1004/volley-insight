"""리그·경기 API가 함께 쓰는 DB 조회 함수들(시즌 고르기, 파라미터 검사, 시즌 합계, 비율 지표)."""
from django.db.models import Count, F, Q, Sum, Value
from django.db.models.functions import Length, Replace
from rest_framework.exceptions import NotFound, ValidationError

from matches.models import Match, PlayerMatchStat, TeamMatchStat

from .models import Season

FINISHED = [Match.Status.FINISHED, Match.Status.CONFIRMED]  # 결과가 있는 경기
REGULAR = "201"  # 정규리그 리그 코드
# 박스스코어에서 합산하는 항목들
BOX_FIELDS = ["attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_attempts", "serve_aces",
              "serve_errors", "block_attempts", "block_success", "block_effective", "block_assist", "receive_attempts",
              "receive_excellent", "receive_errors", "dig_attempts", "dig_success", "set_attempts", "set_success",
              "errors", "warnings"]
# played_sets의 '1' 개수 = 출전 세트 수("11100" → 3). DB 안에서 계산하는 식(전체 길이 - '1'을 지운 길이)
SETS_PLAYED = Length("played_sets") - Length(Replace("played_sets", Value("1"), Value("")))


def resolve_season(code: str | None) -> Season:
    """요청한 시즌(코드), 없으면 경기가 있는 가장 최근 시즌. 찾지 못하면 404."""
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
    """성별 쿼리 파라미터 검사: "M"/"F"만 허용(아니면 400). 필수가 아니고 비어 있으면 None(= 남녀 모두)."""
    if value in (None, "") and not required:
        return None
    if value not in ("M", "F"):
        raise ValidationError({"gender": "M or F"})
    return value


def competition_matches(season, gender, league=REGULAR):
    """시즌·리그(·성별)의 경기 쿼리셋."""
    qs = Match.objects.filter(competition__season=season, competition__league_code=league)
    return qs.filter(gender=gender) if gender else qs


def standings_input(season, gender, league=REGULAR):
    """league.standings.team_standings에 넘길 (팀 목록, 끝난 경기 행들). 아직 경기가 없는 팀도 순위표에 나오게
    팀 목록은 예정 경기까지 포함해 모은다. 경기 행에는 세트 점수를 더한 총 득점(home/away_points)을 붙인다."""
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
    """팀 코드 → 그 팀이 치른 세트 수(순위 자격 '팀 세트의 1/3' 계산용)."""
    out = {}
    for m in competition_matches(season, gender, league).filter(status__in=FINISHED).values(
            "home_sets", "away_sets", home=F("home_team__code"), away=F("away_team__code")):
        sets = m["home_sets"] + m["away_sets"]
        out[m["home"]] = out.get(m["home"], 0) + sets
        out[m["away"]] = out.get(m["away"], 0) + sets
    return out


def player_totals(season, gender, league=REGULAR, **filters):
    """끝난 경기의 (선수, 팀)별 시즌 합계. games는 실제로 한 세트라도 뛴 경기 수("00000" 제외)."""
    qs = PlayerMatchStat.objects.filter(match__competition__season=season, match__competition__league_code=league,
                                        match__status__in=FINISHED, **filters)
    if gender:
        qs = qs.filter(match__gender=gender)
    return list(qs.values(code=F("player__code"), name=F("player__name"), team_code=F("team__code"),
                          team_name=F("team__name"))
                .annotate(games=Count("id", filter=~Q(played_sets="00000")), sets_played=Sum(SETS_PLAYED),
                          points=Sum("points"), **{f: Sum(f) for f in BOX_FIELDS}))


def team_totals(season, gender, league=REGULAR):
    """끝난 경기의 팀별 시즌 합계(박스스코어 합 + 치른 세트 수)."""
    rows = (TeamMatchStat.objects.filter(match__competition__season=season, match__competition__league_code=league,
                                         match__gender=gender, match__status__in=FINISHED)
            .values(code=F("team__code"), name=F("team__name"))
            .annotate(games=Count("id"), points=Sum("points"), opponent_errors=Sum("opponent_errors"),
                      **{f: Sum(f) for f in BOX_FIELDS}))
    sets = team_sets_played(season, gender, league)
    return [{**r, "sets_played": sets.get(r["code"], 0)} for r in rows]


def rates(t: dict) -> dict:
    """박스스코어 합계로 비교 가능한 지표(세트당 수치, 백분율)를 만든다. 분모가 0이면 None.
    공격 효율 = (성공 - 막힘 - 범실) / 시도, 리시브 효율 = (정확 - 실패) / 시도."""
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
