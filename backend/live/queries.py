"""Flink live-metrics가 쓴 테이블에서 실시간 상태를 읽어, live.metrics 메시지와 *같은 모양*으로 만든다.

그래서 처음 받는 스냅숏(GET /api/live/{key}, SSE의 첫 이벤트)과 이후의 갱신을 브라우저가 같은 방식으로 합칠 수 있다:
`state`는 이전 것을 통째로 바꾸고, `points`는 덧붙인다(같은 세트·득점 번호면 바꿔 넣음).
"""
from datetime import timedelta

from django.utils import timezone

from matches.models import Match
from matches.serializers import MatchListSerializer, match_key

from .models import LiveMatchState, LiveRally, LiveSetMetric

STALE_AFTER = timedelta(minutes=30)  # 이만큼 갱신이 없는 LIVE 행은 목록에서 뺀다(수집이 멈춘 경기)
FINISHED_SHOWN = timedelta(hours=2)  # 최근에 끝난 경기는 이 시간 동안 목록에 남긴다


def _f(value):
    """Decimal → float(None은 그대로)."""
    return float(value) if value is not None else None


def state_dict(st: LiveMatchState, metrics):
    """상태 행과 세트 지표 행들을 live_state.summary()와 같은 모양의 딕셔너리로. 팀은 id 대신 코드로 바꾼다."""
    m = st.match
    home, away = m.home_team, m.away_team
    codes = {home.id: home.code, away.id: away.code}
    sides = {home.id: "home", away.id: "away"}
    return {
        "match": match_key(m), "mode": st.mode, "session": st.session, "status": st.status,
        "home": home.code, "away": away.code, "current_set": st.current_set,
        # 끝난 세트는 set_scores에 추가되므로, 지금 세트는 그 점수가 들어오기 전까지 '진행 중'이다
        "set_open": st.status == "LIVE" and len(st.set_scores) < st.current_set,
        "home_points": st.home_points, "away_points": st.away_points,
        "home_sets": st.home_sets, "away_sets": st.away_sets, "set_scores": st.set_scores,
        "serving": codes.get(st.serving_team_id), "home_run": st.home_run, "away_run": st.away_run,
        "home_set_win_prob": _f(st.home_set_win_prob), "home_win_prob": _f(st.home_win_prob),
        "last_play": st.last_play, "last_event_seq": st.last_event_seq,
        "fetched_at": st.fetched_at.isoformat() if st.fetched_at else None,
        "updated_at": st.updated_at.isoformat() if st.updated_at else None,
        "metrics": [{"set": x.set_no, "side": sides.get(x.team_id), "team": codes.get(x.team_id), "points": x.points,
                     "attack_attempts": x.attack_attempts, "attack_success": x.attack_success,
                     "attack_success_rate": _f(x.attack_success_rate), "serve_aces": x.serve_aces,
                     "serve_errors": x.serve_errors, "block_points": x.block_points, "errors": x.errors}
                    for x in metrics],
    }


def point_dict(r: LiveRally, codes):
    """득점 행을 live.metrics의 points 항목과 같은 모양으로. codes: 팀 id → 팀 코드."""
    return {"set": r.set_no, "point_no": r.point_no, "rally_no": r.rally_no, "seq": r.seq,
            "home_score": r.home_score, "away_score": r.away_score, "winner": codes.get(r.winner_team_id),
            "server": codes.get(r.server_team_id), "kind": r.kind,
            "player_code": r.player.code if r.player else None, "player_name": r.player_name,
            "action": r.action_code, "result": r.result_code}


def live_states():
    """실시간 상태 쿼리셋(경기·시즌·두 팀을 함께 가져옴)."""
    return LiveMatchState.objects.select_related("match__competition__season", "match__home_team", "match__away_team")


def snapshot(match: Match):
    """경기 하나의 현재 전체 상태: 경기 정보 + 상태 + 지금까지의 모든 득점. 상태가 없으면 state는 None."""
    st = live_states().filter(match=match).first()
    data = {"type": "snapshot", "match": MatchListSerializer(match).data, "state": None, "points": []}
    if st is None:
        return data
    codes = {match.home_team_id: match.home_team.code, match.away_team_id: match.away_team.code}
    metrics = LiveSetMetric.objects.filter(match=match).order_by("set_no", "team_id")
    data["state"] = state_dict(st, metrics)
    data["points"] = [point_dict(r, codes) for r in
                      LiveRally.objects.filter(match=match).select_related("player").order_by("set_no", "point_no")]
    return data


def current():
    """지금 따라가고 있는 경기(실시간·재생)와 최근 2시간 안에 끝난 경기들. 진행 중 → 실제 경기 → 시작 시각 순."""
    now = timezone.now()
    rows = [st for st in live_states().filter(updated_at__gte=now - FINISHED_SHOWN)
            if st.status == "FINISHED" or st.updated_at >= now - STALE_AFTER]
    rows.sort(key=lambda st: (st.status != "LIVE", st.mode != "LIVE", st.match.scheduled_at))
    metrics = {}
    for x in LiveSetMetric.objects.filter(match_id__in=[st.match_id for st in rows]).order_by("set_no", "team_id"):
        metrics.setdefault(x.match_id, []).append(x)
    from matches.views import base_matches

    matches = {m.id: m for m in base_matches().filter(id__in=[st.match_id for st in rows])}
    return [{"match": MatchListSerializer(matches[st.match_id]).data, "state": state_dict(st, metrics.get(st.match_id, []))}
            for st in rows]
