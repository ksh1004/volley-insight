"""KOVO 문자중계 이벤트로 경기의 실시간 상태를 계산하고, 점수 기반 승리 확률을 구한다(PIPE-06, ANL-07).

순수 파이썬이고 Flink를 import 하지 않는다: flink_jobs/live_metrics.py가 경기마다 상태 딕셔너리 하나를
keyed state로 들고 있다가 kovo.live 메시지마다 `apply_batch`를 부른다. 테스트는 같은 코드를 녹화된 경기에
돌려 검증한다.

이벤트 필드(KOVO /stat/real-time의 `feed`. 코드표는 요구사항 부록 A / frontend constants/volley.js):
  rtcode 팀, rpcode 선수(7자리, 0000000 = 없음), rpname 이름, raction 동작, raresult 결과,
  rgrade +1 랠리를 이기게 한 이벤트 / -1 랠리를 지게 한 이벤트 / 0 둘 다 아님, rpage 세트 안 랠리 번호.
  ST / ET는 세트 시작과 끝 표시. 랠리는 +1 이벤트를 낸 팀이 이긴다(spark_jobs/curate_kovo.py와 같은 규칙.
  이 규칙으로 2025-26 시즌 모든 공식 세트 점수가 그대로 재현된다).
이벤트의 정체(identity): seq = 세트번호 × 10000 + 그 세트 목록에서의 위치. KOVO는 세트 목록에 뒤로 덧붙이기만
하므로, 이벤트가 겹쳐 오는 메시지(수집기 재시작, 같은 메시지 재처리)가 와도 상태가 바뀌지 않는다.
"""
from functools import lru_cache

SEQ_BASE = 10000
ATTACKS = {"AA", "AC", "AT", "AE", "AQ", "AW", "AZ"}  # 공격 동작 코드들(오픈, 속공, 시간차, 후위 등)
SERVES = {"VV", "VS"}  # 서브 동작 코드
SETS_TO_WIN = 3
# 세트·팀별로 세는 기록 항목(화면의 세트별 비교표)
METRIC_FIELDS = ("points", "attack_attempts", "attack_success", "serve_aces", "serve_errors", "block_points", "errors")


def seq_of(set_no, pos):
    """(세트, 위치) → 경기 전체에서 유일한 이벤트 순번."""
    return set_no * SEQ_BASE + pos


def set_target(set_no):
    """세트를 이기는 점수: 5세트는 15점, 나머지는 25점(둘 다 2점 차 이상)."""
    return 15 if set_no == 5 else 25


def is_error(ev):
    """범실 이벤트인가(결과 코드가 er로 시작: er1 네트터치 … 등)."""
    return str(ev.get("raresult") or "").startswith("er")


def player_code(ev):
    """이벤트의 선수 코드. 7자리 숫자가 아니거나 0000000(선수 없음)이면 None."""
    code = str(ev.get("rpcode") or "")
    return code if len(code) == 7 and code.isdigit() and code != "0000000" else None


# ---------------------------------------------------------------- 승리 확률(ANL-07)
# 원리: 랠리 하나를 이길 확률을 '누가 서브하느냐'에 따라 두 개로 두고(서브권이 승패를 크게 좌우함),
# 지금 점수에서 세트를 이길 확률을 재귀로, 세트 확률로 경기를 이길 확률을 다시 재귀로 계산한다.
def rally_probs(rates):
    """두 팀의 사이드아웃률·브레이크율(0~1)로 P(홈이 랠리를 이김 | 홈 서브), P(홈이 랠리를 이김 | 원정 서브)를 구한다.
    사이드아웃 = 상대 서브를 받아 점수를 따는 것, 브레이크 = 내 서브 랠리를 이기는 것.
    각 확률을 두 팀 기록으로 한 번씩 추정해 평균 내고, 극단값을 막기 위해 0.1~0.9로 자른다."""
    p_hs = (rates["home_break"] + 1 - rates["away_sideout"]) / 2
    p_as = (rates["home_sideout"] + 1 - rates["away_break"]) / 2
    clamp = lambda p: min(0.9, max(0.1, p))  # noqa: E731
    return clamp(p_hs), clamp(p_as)


def _deuce(p_hs, p_as):
    """듀스 구간(목표-1 점 이상에서)의 승리 확률을 연립방정식으로 바로 푼다(무한히 이어질 수 있어 재귀로는 못 품).
    돌려주는 값: Dh(동점·홈 서브), Da(동점·원정 서브), X(홈이 1점 앞서고 서브), Y(원정이 1점 앞서고 서브).
    규칙: 2점 차로 이겨야 하고, 랠리를 이긴 팀이 다음 서브를 한다."""
    a11, a12, b1 = 1 - (1 - p_hs) * p_as, -p_hs * (1 - p_hs), p_hs * p_hs
    a21, a22, b2 = -(1 - p_as) * p_as, 1 - p_as * (1 - p_hs), p_as * p_hs
    det = a11 * a22 - a12 * a21
    dh = (b1 * a22 - a12 * b2) / det  # 크래머 공식
    da = (a11 * b2 - a21 * b1) / det
    return dh, da, p_hs + (1 - p_hs) * da, p_as * dh


@lru_cache(maxsize=64)
def _set_table(p_hs, p_as, target):
    """(랠리 확률, 목표 점수) 조합마다 '점수 → 세트 승리 확률' 함수를 만들어 캐시한다(같은 경기에선 계속 재사용)."""
    dh, da, x, y = _deuce(p_hs, p_as)

    @lru_cache(maxsize=None)
    def f(h, a, serving):
        """홈 h점, 원정 a점, serving이 서브할 때 홈이 세트를 이길 확률."""
        if h >= target and h - a >= 2:
            return 1.0
        if a >= target and a - h >= 2:
            return 0.0
        if h >= target - 1 and a >= target - 1:  # 듀스 구간: 미리 푼 값 사용
            if h == a:
                return dh if serving == "home" else da
            return x if h > a else y
        p = p_hs if serving == "home" else p_as
        # 이번 랠리를 홈이 이기면 (h+1, a, 홈 서브), 지면 (h, a+1, 원정 서브)
        return p * f(h + 1, a, "home") + (1 - p) * f(h, a + 1, "away")
    return f


def set_win_prob(h, a, serving, p_hs, p_as, target=25):
    """지금 점수에서 홈이 세트를 이길 확률. 캐시가 잘 맞도록 확률은 소수 넷째 자리로 반올림해 쓴다."""
    f = _set_table(round(p_hs, 4), round(p_as, 4), target)
    if serving not in ("home", "away"):  # 세트 첫 서브 전: 누가 서브할지 모르므로 두 경우의 평균
        return (f(h, a, "home") + f(h, a, "away")) / 2
    return f(h, a, serving)


def match_win_prob(home_sets, away_sets, next_set, current_set_prob, p_hs, p_as):
    """홈이 경기를 이길 확률. `current_set_prob`는 진행 중인 세트(세트 번호 next_set - 1)를 홈이 이길 확률이고,
    세트 사이(진행 중인 세트 없음)라면 None이다."""
    @lru_cache(maxsize=None)
    def m(hs, as_, n):
        """세트 스코어 hs:as_, 다음이 n세트일 때 홈의 경기 승리 확률."""
        if hs >= SETS_TO_WIN:
            return 1.0
        if as_ >= SETS_TO_WIN:
            return 0.0
        q = set_win_prob(0, 0, None, p_hs, p_as, set_target(n))  # 새 세트를 0:0에서 이길 확률
        return q * m(hs + 1, as_, n + 1) + (1 - q) * m(hs, as_ + 1, n + 1)

    if current_set_prob is None:
        return m(home_sets, away_sets, next_set)
    q = current_set_prob
    return q * m(home_sets + 1, away_sets, next_set) + (1 - q) * m(home_sets, away_sets + 1, next_set)


# ---------------------------------------------------------------- 경기 상태
def new_state(match, home, away, mode, session, rates):
    """경기 하나의 빈 상태를 만든다. mode: LIVE/REPLAY, session: 재생 세션 ID, rates: 두 팀의 시즌 랠리 기록(확률용).
    set_open: 세트가 진행 중인지, run: 연속 득점(팀, 개수), rally_errors: 지금 랠리에서 나온 범실 이벤트들."""
    return {
        "match": match, "home": home, "away": away, "mode": mode, "session": session, "rates": rates,
        "status": "LIVE", "set": 0, "set_open": False, "points": {"home": 0, "away": 0},
        "sets": {"home": 0, "away": 0}, "set_scores": [], "serving": None, "run": {"side": None, "n": 0},
        "rally": None, "rally_errors": [], "last_seq": 0, "metrics": {}, "last_point": None,
        "set_prob": None, "match_prob": None,
    }


def _side(state, team_code):
    """팀 코드 → "home" / "away"(이 경기 팀이 아니면 None)."""
    if team_code == state["home"]:
        return "home"
    if team_code == state["away"]:
        return "away"
    return None


def _metric(state, set_no, side):
    """세트·팀의 기록 칸(없으면 0으로 만든다). JSON으로 저장되므로 세트 번호는 문자열 키."""
    sets = state["metrics"].setdefault(str(set_no), {})
    return sets.setdefault(side, dict.fromkeys(METRIC_FIELDS, 0))


def _open_set(state, set_no):
    """새 세트의 이벤트가 오면 세트를 연다(점수·서브·연속 득점 초기화)."""
    if state["set_open"] and state["set"] != set_no:
        _close_set(state)  # 세트 종료 이벤트를 놓친 경우: 새 세트의 이벤트가 옛 세트를 닫는다
    if state["set"] != set_no:
        state.update(set=set_no, set_open=True, points={"home": 0, "away": 0}, serving=None,
                     run={"side": None, "n": 0}, rally=None, rally_errors=[])
        _metric(state, set_no, "home"), _metric(state, set_no, "away")


def _close_set(state):
    """세트를 닫는다: 점수가 높은 팀의 세트 수를 올리고, 3세트를 이긴 팀이 있으면 경기 종료."""
    if not state["set_open"]:
        return
    h, a = state["points"]["home"], state["points"]["away"]
    if h != a:
        state["sets"]["home" if h > a else "away"] += 1
        state["set_scores"].append([h, a])
    state["set_open"] = False
    state["serving"] = None
    if max(state["sets"].values()) >= SETS_TO_WIN:
        state["status"] = "FINISHED"


def _point(state, set_no, pos, ev, side):
    """side 팀의 득점을 반영하고, 화면 피드에 보낼 득점 정보(누가 어떻게 땄는지)를 만든다."""
    state["points"][side] += 1
    _metric(state, set_no, side)["points"] += 1
    run = state["run"]
    state["run"] = {"side": side, "n": run["n"] + 1 if run["side"] == side else 1}  # 연속 득점
    action = ev.get("raction") or ""
    # 득점 종류: 공격, 블로킹(BB), 서브 에이스, 상대 범실(OP), 기타
    kind = ("ATTACK" if action in ATTACKS else "BLOCK" if action == "BB" else "ACE" if action in SERVES
            else "OPP_ERROR" if action == "OP" else "OTHER")
    cause = ev
    if kind == "OPP_ERROR" and state["rally_errors"]:
        cause = state["rally_errors"][-1]  # 상대 범실은 목록에서 득점 이벤트보다 먼저 나왔다: 그 선수를 원인으로
    point = {
        "set": set_no, "point_no": state["points"]["home"] + state["points"]["away"],
        "rally_no": int(ev.get("rpage") or 0), "seq": seq_of(set_no, pos),
        "home_score": state["points"]["home"], "away_score": state["points"]["away"],
        "winner": state[side], "server": state[state["serving"]] if state["serving"] else None, "kind": kind,
        "player_code": player_code(cause), "player_name": (cause.get("rpname") or "").strip() if player_code(cause) else "",
        "action": cause.get("raction") or "", "result": cause.get("raresult") or "",
    }
    state["serving"] = side  # 랠리를 이긴 팀이 다음 서브를 한다
    state["last_point"] = point
    return point


def apply_event(state, set_no, pos, ev):
    """이벤트 하나를 반영한다. 이 이벤트로 생기거나 완성된 득점(딕셔너리)을 돌려주고, 없으면 None."""
    seq = seq_of(set_no, pos)
    if seq <= state["last_seq"] or state["status"] == "FINISHED":
        return None  # 이미 반영한 이벤트(중복) 또는 끝난 경기
    state["last_seq"] = seq
    action = ev.get("raction") or ""
    if action == "ET":  # 세트 종료
        if state["set"] == set_no:
            _close_set(state)
        return None
    _open_set(state, set_no)
    if action == "ST":  # 세트 시작
        return None
    side = _side(state, ev.get("rtcode"))
    if side is None:
        return None
    rally = int(ev.get("rpage") or 0)
    if rally != state["rally"]:  # 새 랠리 시작: 범실 목록 비움
        state["rally"], state["rally_errors"] = rally, []
    m = _metric(state, set_no, side)
    if action in SERVES:
        state["serving"] = side
        m["serve_aces"] += int(ev.get("raresult") == "suc")
        m["serve_errors"] += int(is_error(ev))
    elif action in ATTACKS:
        m["attack_attempts"] += 1
        m["attack_success"] += int(ev.get("raresult") == "suc")
    elif action == "BB":  # 블로킹
        m["block_points"] += int(ev.get("raresult") == "suc")
    if is_error(ev):
        m["errors"] += 1
        state["rally_errors"].append(ev)
        last = state["last_point"]
        # 범실이 상대 득점 '뒤에' 나온 경우: 직전 득점(상대 범실)의 원인 선수로 채워 넣고, 바뀐 득점을 돌려준다
        if (last and last["kind"] == "OPP_ERROR" and last["set"] == set_no and last["rally_no"] == rally
                and not last["player_code"] and player_code(ev)):
            last.update(player_code=player_code(ev), player_name=(ev.get("rpname") or "").strip(),
                        action=action, result=ev.get("raresult") or "")
            return last
    if int(ev.get("rgrade") or 0) > 0:  # +1 이벤트 = 이 팀이 랠리를 이겼다
        return _point(state, set_no, pos, ev, side)
    return None


def update_probs(state):
    """현재 상태로 세트·경기 승리 확률을 다시 계산한다. 팀 기록(rates)이 없으면 확률을 비운다."""
    if not state.get("rates"):
        state["set_prob"] = state["match_prob"] = None
        return
    if state["status"] == "FINISHED":
        state["set_prob"] = None
        state["match_prob"] = 1.0 if state["sets"]["home"] > state["sets"]["away"] else 0.0
        return
    p_hs, p_as = rally_probs(state["rates"])
    hs, as_ = state["sets"]["home"], state["sets"]["away"]
    if state["set_open"]:
        sp = set_win_prob(state["points"]["home"], state["points"]["away"], state["serving"], p_hs, p_as,
                          set_target(state["set"]))
        state["set_prob"] = round(sp, 4)
        state["match_prob"] = round(match_win_prob(hs, as_, state["set"] + 1, sp, p_hs, p_as), 4)
    else:
        state["set_prob"] = None
        state["match_prob"] = round(match_win_prob(hs, as_, max(state["set"], 0) + 1, None, p_hs, p_as), 4)


def apply_batch(state, set_no, start, events):
    """메시지 하나의 이벤트들(세트 `set_no` 목록의 start, start+1, ... 위치)을 반영한다.
    생기거나 바뀐 득점들을 순서대로 돌려준다(한 득점이 두 번 바뀌어도 한 번만)."""
    changed = {}
    for i, ev in enumerate(events):
        point = apply_event(state, set_no, start + i, ev)
        if point is not None:
            changed[(point["set"], point["point_no"])] = point
    update_probs(state)
    return [dict(p) for p in changed.values()]


def summary(state):
    """상태 중 공개할 부분(사이트에 보여 줄 것)만, 내부 기록용 값은 빼고 돌려준다.
    live_match_state 테이블과 SSE 메시지의 모양이 이것이다."""
    h, a = state["home"], state["away"]
    metrics = []
    for set_no, sides in sorted(state["metrics"].items(), key=lambda kv: int(kv[0])):
        for side, m in sides.items():
            rate = round(100 * m["attack_success"] / m["attack_attempts"], 2) if m["attack_attempts"] else None
            metrics.append({"set": int(set_no), "side": side, "team": state[side], **m, "attack_success_rate": rate})
    return {
        "match": state["match"], "mode": state["mode"], "session": state["session"], "status": state["status"],
        "home": h, "away": a, "current_set": state["set"], "set_open": state["set_open"],
        "home_points": state["points"]["home"], "away_points": state["points"]["away"],
        "home_sets": state["sets"]["home"], "away_sets": state["sets"]["away"], "set_scores": state["set_scores"],
        "serving": state[state["serving"]] if state["serving"] else None,
        "home_run": state["run"]["n"] if state["run"]["side"] == "home" else 0,
        "away_run": state["run"]["n"] if state["run"]["side"] == "away" else 0,
        "home_set_win_prob": state["set_prob"], "home_win_prob": state["match_prob"],
        "last_play": state["last_point"], "last_event_seq": state["last_seq"], "metrics": metrics,
    }
