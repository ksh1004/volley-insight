"""경기 API(설계서 1.11): 경기 목록, 경기 상세(박스스코어·프리뷰), 세트별 문자중계."""
import re
from datetime import date, datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics import queries as aq
from analytics.models import MatchPrediction
from league.queries import BOX_FIELDS, FINISHED, gender_param, rates, resolve_season
from ops.models import Correction, QualityIssue

from .models import Match, PlayerMatchStat, RallyEvent, SetScore, TeamMatchStat
from .serializers import MatchListSerializer, match_key, team_ref

SOURCE = {"name": "KOVO 한국배구연맹", "url": "https://www.kovo.co.kr"}  # 출처 표시(WEB-14): 모든 API 응답에 붙인다


class MatchPagination(PageNumberPagination):
    """경기 목록 페이지 나누기: 기본 50개, ?page_size=로 최대 300개."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 300  # 남녀부 한 달 치 경기가 한 페이지에 들어가는 크기(달력 화면)


def base_matches():
    """경기 쿼리셋의 기본형: 시즌·팀을 함께 가져오고(select_related), 세트 점수와 예측은 미리 묶어 가져온다
    (prefetch_related). 목록을 그릴 때 경기마다 쿼리가 또 나가는 N+1 문제를 막는다."""
    return (Match.objects.select_related("competition__season", "home_team", "away_team")
            .prefetch_related(Prefetch("sets", queryset=SetScore.objects.order_by("set_no")),
                              Prefetch("predictions", queryset=MatchPrediction.objects.order_by("-predicted_at"))))


def get_match(key: str) -> Match:
    """경기 키("022-3")로 경기를 찾는다. 형식이 틀리면 400, 없으면 404."""
    m = re.fullmatch(r"(\d{3})-(\d+)", key)
    if not m:
        raise ValidationError({"key": "season-game number, e.g. 022-3"})
    return get_object_or_404(base_matches(), competition__season__code=m[1], game_no=int(m[2]))


def kst_day_range(day: date):
    """한국 날짜 하루의 [시작, 다음 날 시작) 시각 범위(시간대가 붙은 값). 날짜 필터에 쓴다."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


class MatchListView(APIView):
    """GET /api/matches?season=&gender=&team=&month=YYYY-MM&date=YYYY-MM-DD&league=&status=finished|scheduled&order=desc

    필터를 조합해 경기 목록을 페이지 단위로 돌려준다. season 없이 date나 status만 주면 시즌을 가리지 않는다
    (홈 화면의 '오늘 경기', '다가오는 경기'가 이렇게 쓴다). scheduled는 시작 3시간 전 경기까지 포함(진행 중 경기)."""

    def get(self, request):
        p = request.query_params
        qs = base_matches()
        if p.get("season") or not (p.get("date") or p.get("status")):
            qs = qs.filter(competition__season=resolve_season(p.get("season")))
        if gender := gender_param(p.get("gender")):
            qs = qs.filter(gender=gender)
        if team := p.get("team"):
            qs = qs.filter(Q(home_team__code=team) | Q(away_team__code=team))
        if league := p.get("league"):
            qs = qs.filter(competition__league_code=league)
        if month := p.get("month"):
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValidationError({"month": "YYYY-MM"})
            y, mo = map(int, month.split("-"))
            start, _ = kst_day_range(date(y, mo, 1))
            end, _ = kst_day_range(date(y + mo // 12, mo % 12 + 1, 1))  # 다음 달 1일(12월이면 다음 해 1월)
            qs = qs.filter(scheduled_at__gte=start, scheduled_at__lt=end)
        if day := p.get("date"):
            try:
                start, end = kst_day_range(date.fromisoformat(day))
            except ValueError:
                raise ValidationError({"date": "YYYY-MM-DD"})
            qs = qs.filter(scheduled_at__gte=start, scheduled_at__lt=end)
        status = p.get("status")
        if status == "finished":
            qs = qs.filter(status__in=FINISHED)
        elif status == "scheduled":
            qs = qs.filter(status__in=[Match.Status.SCHEDULED, Match.Status.LIVE], scheduled_at__gte=timezone.now() - timedelta(hours=3))
        elif status:
            raise ValidationError({"status": "finished or scheduled"})
        qs = qs.order_by("-scheduled_at" if p.get("order") == "desc" else "scheduled_at", "game_no")
        paginator = MatchPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MatchListSerializer(page, many=True).data)


def box(row, extra=()):
    """기록 행(모델 객체)에서 박스스코어 필드와 extra 필드를 딕셔너리로 꺼낸다."""
    d = {f: getattr(row, f) for f in BOX_FIELDS}
    d.update({f: getattr(row, f) for f in extra})
    return d


def data_notes(m, key):
    """이 경기 데이터의 알려진 문제(해결 안 된 ERROR 품질 문제, PIPE-10)와 관리자가 고친 값의 수(ADM-03).
    화면이 빈 곳을 말없이 보여 주지 않고 '기록 일부 누락' 같은 안내를 띄울 수 있게 한다."""
    by_rule = {}
    for i in (QualityIssue.objects.filter(match=m, severity=QualityIssue.Severity.ERROR)
              .exclude(status=QualityIssue.Status.RESOLVED).order_by("set_no", "rule")):
        note = by_rule.setdefault(i.rule, {"rule": i.rule, "label": i.get_rule_display(), "sets": []})
        if i.set_no:
            note["sets"].append(i.set_no)
    corrected = Correction.objects.filter(is_active=True).filter(
        Q(target=Correction.Target.MATCH, key=key) | Q(key__startswith=f"{key}/")).count()
    return {"issues": list(by_rule.values()), "corrected": corrected}


def live_brief(m):
    """이 경기가 실시간으로 중계됐거나(되고 있거나) 재생된 적이 있는지. 있으면 화면에 '실시간 보기' 링크를 단다."""
    from live.models import LiveMatchState

    st = LiveMatchState.objects.filter(match=m).only("mode", "status", "updated_at").first()
    return {"mode": st.mode, "status": st.status, "updated_at": st.updated_at} if st else None


class MatchDetailView(APIView):
    """GET /api/matches/{시즌}-{경기번호}: 경기 상세(WEB-03) — 세트 점수, 팀·선수 박스스코어, 문자중계가 있는 세트,
    실시간 기록 여부, 데이터 안내, 그리고 경기 프리뷰(예측, 상대 전적, 두 팀의 최근 폼)."""

    def get(self, request, key):
        m = get_match(key)
        n_sets = len(m.sets.all())
        teams = {t.team_id: t for t in TeamMatchStat.objects.filter(match=m)}
        side = {m.home_team_id: "home", m.away_team_id: "away"}  # 팀 id → 홈/원정
        team_stats = {}
        for team_id, t in teams.items():
            d = box(t, ["points", "opponent_errors"])
            d["sets_played"] = n_sets
            team_stats[side[team_id]] = {**d, **rates(d)}
        players = {"home": [], "away": []}
        for p in (PlayerMatchStat.objects.filter(match=m).exclude(played_sets="00000")
                  .select_related("player").order_by("-points", "player__name")):
            row = box(p, ["points", "back_number", "position", "is_captain", "played_sets"])
            row["sets_played"] = p.played_sets.count("1")
            players[side.get(p.team_id, "home")].append({"player": {"code": p.player.code, "name": p.player.name},
                                                         **row, **rates(row)})
        rally_sets = list(RallyEvent.objects.filter(match=m).values_list("set_no", flat=True).distinct().order_by("set_no"))
        return Response({
            **MatchListSerializer(m).data,
            "city": m.city, "duration_min": m.duration_min, "spectators": m.spectators,
            "referee_main": m.referee_main, "referee_sub": m.referee_sub,
            "set_details": [{"set_no": s.set_no, "home_points": s.home_points, "away_points": s.away_points,
                             "duration_min": s.duration_min} for s in m.sets.all()],
            "team_stats": team_stats, "players": players, "rally_sets": rally_sets,
            "live": live_brief(m),
            "data_notes": data_notes(m, key),
            "preview": {
                "prediction": aq.latest_prediction(m),
                "head_to_head": aq.head_to_head(m),
                "home": aq.team_form(m.home_team, m.scheduled_at, m.competition.season),
                "away": aq.team_form(m.away_team, m.scheduled_at, m.competition.season),
            },
            "source": SOURCE,
        })


class RallyView(APIView):
    """GET /api/matches/{key}/rallies?set=N: 한 세트의 문자중계 이벤트와 이벤트마다의 누적 점수."""

    def get(self, request, key):
        m = get_match(key)
        try:
            set_no = int(request.query_params.get("set", 1))
        except ValueError:
            raise ValidationError({"set": "1-5"})
        events = (RallyEvent.objects.filter(match=m, set_no=set_no).select_related("team", "player")
                  .order_by("event_no"))
        return Response({
            "key": match_key(m), "set_no": set_no,
            "home": team_ref(m.home_team), "away": team_ref(m.away_team),
            "events": [{"event_no": e.event_no, "rally_no": e.rally_no,
                        "team": e.team.code if e.team else None,
                        "player": {"code": e.player.code, "name": e.player.name} if e.player else None,
                        "back_number": e.back_number, "action": e.action_code, "result": e.result_code,
                        "grade": e.grade, "home_score": e.home_score, "away_score": e.away_score}
                       for e in events],
            "source": SOURCE,
        })

