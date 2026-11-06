"""Live match state from KOVO play-by-play events, and the point-based win probability (PIPE-06, ANL-07).

Pure Python, no Flink imports: flink_jobs/live_metrics.py keeps one state dict per match in keyed state and
calls `apply_batch` for every kovo.live message; the tests run the same code on recorded games.

Event fields (KOVO /stat/real-time `feed`, codes in requirements appendix A / frontend constants/volley.js):
  rtcode team, rpcode player (7 digits, 0000000 = none), rpname name, raction action, raresult result,
  rgrade +1 the event that won the rally / -1 the event that lost it / 0 neither, rpage rally number in the set.
  ST / ET mark the start and end of a set. A rally is won by the team of its +1 event (same rule as
  spark_jobs/curate_kovo.py, which reproduces every official set score of 2025-26).
Event identity: seq = set_no * 10000 + position in that set's feed. KOVO only appends to a set's feed, so a
message that repeats events (collector restart, replay of the same message) changes nothing.
"""
from functools import lru_cache

SEQ_BASE = 10000
ATTACKS = {"AA", "AC", "AT", "AE", "AQ", "AW", "AZ"}
SERVES = {"VV", "VS"}
SETS_TO_WIN = 3
METRIC_FIELDS = ("points", "attack_attempts", "attack_success", "serve_aces", "serve_errors", "block_points", "errors")


def seq_of(set_no, pos):
    return set_no * SEQ_BASE + pos


def set_target(set_no):
    return 15 if set_no == 5 else 25


def is_error(ev):
    return str(ev.get("raresult") or "").startswith("er")


def player_code(ev):
    code = str(ev.get("rpcode") or "")
    return code if len(code) == 7 and code.isdigit() and code != "0000000" else None


# ---------------------------------------------------------------- win probability (ANL-07)
def rally_probs(rates):
    """P(home wins a rally | home serves), P(home wins a rally | away serves) from the two teams' side-out and
    break rates (fractions). Each is estimated from both teams and averaged."""
    p_hs = (rates["home_break"] + 1 - rates["away_sideout"]) / 2
    p_as = (rates["home_sideout"] + 1 - rates["away_break"]) / 2
    clamp = lambda p: min(0.9, max(0.1, p))  # noqa: E731
    return clamp(p_hs), clamp(p_as)


def _deuce(p_hs, p_as):
    """Win chances from tied scores past target-1 (Dh home serving, Da away serving) and from a one-point lead
    (X home leads and serves, Y away leads and serves). Win by two, the rally winner serves next."""
    a11, a12, b1 = 1 - (1 - p_hs) * p_as, -p_hs * (1 - p_hs), p_hs * p_hs
    a21, a22, b2 = -(1 - p_as) * p_as, 1 - p_as * (1 - p_hs), p_as * p_hs
    det = a11 * a22 - a12 * a21
    dh = (b1 * a22 - a12 * b2) / det
    da = (a11 * b2 - a21 * b1) / det
    return dh, da, p_hs + (1 - p_hs) * da, p_as * dh


@lru_cache(maxsize=64)
def _set_table(p_hs, p_as, target):
    dh, da, x, y = _deuce(p_hs, p_as)

    @lru_cache(maxsize=None)
    def f(h, a, serving):
        if h >= target and h - a >= 2:
            return 1.0
        if a >= target and a - h >= 2:
            return 0.0
        if h >= target - 1 and a >= target - 1:
            if h == a:
                return dh if serving == "home" else da
            return x if h > a else y
        p = p_hs if serving == "home" else p_as
        return p * f(h + 1, a, "home") + (1 - p) * f(h, a + 1, "away")
    return f


def set_win_prob(h, a, serving, p_hs, p_as, target=25):
    f = _set_table(round(p_hs, 4), round(p_as, 4), target)
    if serving not in ("home", "away"):  # before the first serve of a set
        return (f(h, a, "home") + f(h, a, "away")) / 2
    return f(h, a, serving)


def match_win_prob(home_sets, away_sets, next_set, current_set_prob, p_hs, p_as):
    """Chance the home team wins the match. `current_set_prob` is the home team's chance in the set being played
    (set number next_set - 1); None when no set is in progress (between sets)."""
    @lru_cache(maxsize=None)
    def m(hs, as_, n):
        if hs >= SETS_TO_WIN:
            return 1.0
        if as_ >= SETS_TO_WIN:
            return 0.0
        q = set_win_prob(0, 0, None, p_hs, p_as, set_target(n))
        return q * m(hs + 1, as_, n + 1) + (1 - q) * m(hs, as_ + 1, n + 1)

    if current_set_prob is None:
        return m(home_sets, away_sets, next_set)
    q = current_set_prob
    return q * m(home_sets + 1, away_sets, next_set) + (1 - q) * m(home_sets, away_sets + 1, next_set)


# ---------------------------------------------------------------- state
def new_state(match, home, away, mode, session, rates):
    return {
        "match": match, "home": home, "away": away, "mode": mode, "session": session, "rates": rates,
        "status": "LIVE", "set": 0, "set_open": False, "points": {"home": 0, "away": 0},
        "sets": {"home": 0, "away": 0}, "set_scores": [], "serving": None, "run": {"side": None, "n": 0},
        "rally": None, "rally_errors": [], "last_seq": 0, "metrics": {}, "last_point": None,
        "set_prob": None, "match_prob": None,
    }


def _side(state, team_code):
    if team_code == state["home"]:
        return "home"
    if team_code == state["away"]:
        return "away"
    return None


def _metric(state, set_no, side):
    sets = state["metrics"].setdefault(str(set_no), {})
    return sets.setdefault(side, {f: 0 for f in METRIC_FIELDS})


def _open_set(state, set_no):
    if state["set_open"] and state["set"] != set_no:
        _close_set(state)  # the end-of-set event was missed; the new set's events close the old one
    if state["set"] != set_no:
        state.update(set=set_no, set_open=True, points={"home": 0, "away": 0}, serving=None,
                     run={"side": None, "n": 0}, rally=None, rally_errors=[])
        _metric(state, set_no, "home"), _metric(state, set_no, "away")


def _close_set(state):
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
    state["points"][side] += 1
    _metric(state, set_no, side)["points"] += 1
    run = state["run"]
    state["run"] = {"side": side, "n": run["n"] + 1 if run["side"] == side else 1}
    action = ev.get("raction") or ""
    kind = ("ATTACK" if action in ATTACKS else "BLOCK" if action == "BB" else "ACE" if action in SERVES
            else "OPP_ERROR" if action == "OP" else "OTHER")
    cause = ev
    if kind == "OPP_ERROR" and state["rally_errors"]:
        cause = state["rally_errors"][-1]  # the opponent's error came before the point in the feed
    point = {
        "set": set_no, "point_no": state["points"]["home"] + state["points"]["away"],
        "rally_no": int(ev.get("rpage") or 0), "seq": seq_of(set_no, pos),
        "home_score": state["points"]["home"], "away_score": state["points"]["away"],
        "winner": state[side], "server": state[state["serving"]] if state["serving"] else None, "kind": kind,
        "player_code": player_code(cause), "player_name": (cause.get("rpname") or "").strip() if player_code(cause) else "",
        "action": cause.get("raction") or "", "result": cause.get("raresult") or "",
    }
    state["serving"] = side  # the rally winner serves next
    state["last_point"] = point
    return point


def apply_event(state, set_no, pos, ev):
    """Apply one event. Returns the point it scored or completed (a dict), or None."""
    seq = seq_of(set_no, pos)
    if seq <= state["last_seq"] or state["status"] == "FINISHED":
        return None
    state["last_seq"] = seq
    action = ev.get("raction") or ""
    if action == "ET":
        if state["set"] == set_no:
            _close_set(state)
        return None
    _open_set(state, set_no)
    if action == "ST":
        return None
    side = _side(state, ev.get("rtcode"))
    if side is None:
        return None
    rally = int(ev.get("rpage") or 0)
    if rally != state["rally"]:
        state["rally"], state["rally_errors"] = rally, []
    m = _metric(state, set_no, side)
    if action in SERVES:
        state["serving"] = side
        m["serve_aces"] += int(ev.get("raresult") == "suc")
        m["serve_errors"] += int(is_error(ev))
    elif action in ATTACKS:
        m["attack_attempts"] += 1
        m["attack_success"] += int(ev.get("raresult") == "suc")
    elif action == "BB":
        m["block_points"] += int(ev.get("raresult") == "suc")
    if is_error(ev):
        m["errors"] += 1
        state["rally_errors"].append(ev)
        last = state["last_point"]
        # the error was listed after the opponent's point: name the player on that point
        if (last and last["kind"] == "OPP_ERROR" and last["set"] == set_no and last["rally_no"] == rally
                and not last["player_code"] and player_code(ev)):
            last.update(player_code=player_code(ev), player_name=(ev.get("rpname") or "").strip(),
                        action=action, result=ev.get("raresult") or "")
            return last
    if int(ev.get("rgrade") or 0) > 0:
        return _point(state, set_no, pos, ev, side)
    return None


def update_probs(state):
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
    """Apply the events of one message (positions start, start+1, ... in set `set_no`'s feed).
    Returns the points scored or changed, in order (a point changed twice appears once)."""
    changed = {}
    for i, ev in enumerate(events):
        point = apply_event(state, set_no, start + i, ev)
        if point is not None:
            changed[(point["set"], point["point_no"])] = point
    update_probs(state)
    return [dict(p) for p in changed.values()]


def summary(state):
    """The public part of the state (what the site shows), without the bookkeeping."""
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
