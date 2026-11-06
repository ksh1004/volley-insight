"""Match APIs (design.md 1.11): list, detail, play-by-play."""
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

SOURCE = {"name": "KOVO 한국배구연맹", "url": "https://www.kovo.co.kr"}  # WEB-14


class MatchPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 300  # a whole month of both genders fits in one page


def base_matches():
    return (Match.objects.select_related("competition__season", "home_team", "away_team")
            .prefetch_related(Prefetch("sets", queryset=SetScore.objects.order_by("set_no")),
                              Prefetch("predictions", queryset=MatchPrediction.objects.order_by("-predicted_at"))))


def get_match(key: str) -> Match:
    m = re.fullmatch(r"(\d{3})-(\d+)", key)
    if not m:
        raise ValidationError({"key": "season-game number, e.g. 022-3"})
    return get_object_or_404(base_matches(), competition__season__code=m[1], game_no=int(m[2]))


def kst_day_range(day: date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


class MatchListView(APIView):
    """GET /api/matches?season=&gender=&team=&month=YYYY-MM&date=YYYY-MM-DD&league=&status=finished|scheduled&order=desc

    Without `season`, `date` and `status` work across seasons (used by the home page)."""

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
            end, _ = kst_day_range(date(y + mo // 12, mo % 12 + 1, 1))
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
    d = {f: getattr(row, f) for f in BOX_FIELDS}
    d.update({f: getattr(row, f) for f in extra})
    return d


def data_notes(m, key):
    """Known problems of this match's data (unresolved ERROR quality issues, PIPE-10) and how many of its values an
    admin corrected (ADM-03), so the page can say so instead of silently showing gaps."""
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
    """Whether the match is (or was) followed live or replayed, for the link to its live page."""
    from live.models import LiveMatchState

    st = LiveMatchState.objects.filter(match=m).only("mode", "status", "updated_at").first()
    return {"mode": st.mode, "status": st.status, "updated_at": st.updated_at} if st else None


class MatchDetailView(APIView):
    """GET /api/matches/{season}-{game_no}: sets, team and player box scores (WEB-03)."""

    def get(self, request, key):
        m = get_match(key)
        n_sets = len(m.sets.all())
        teams = {t.team_id: t for t in TeamMatchStat.objects.filter(match=m)}
        side = {m.home_team_id: "home", m.away_team_id: "away"}
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
    """GET /api/matches/{key}/rallies?set=N: play-by-play of one set with the running score."""

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

