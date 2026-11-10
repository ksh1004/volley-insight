"""팀 순위와 선수 순위(WEB-05)를 끝난 경기로 계산한다.

팀 순서는 V-리그 규칙을 따른다: 승점(3:0·3:1 승 3점, 3:2 승 2점, 2:3 패 1점, 그 외 패 0점)
→ 승수 → 세트 득실률 → 점수 득실률. 마지막 기준인 상대 전적은 적용하지 않는다.
DB 없이 규칙을 테스트할 수 있게 평범한 행(딕셔너리)을 받는 순수 함수로 만들었다.
"""
from dataclasses import dataclass, field


def match_points(won_sets: int, lost_sets: int) -> int:
    """경기 하나로 얻는 승점."""
    if won_sets == 3:
        return 3 if lost_sets <= 1 else 2
    return 1 if (won_sets == 2 and lost_sets == 3) else 0


def ratio(a, b):
    """득실률 = 얻은 것 / 잃은 것. 잃은 게 없으면 None(화면에 "MAX"로 보이고 맨 앞으로 정렬)."""
    return round(a / b, 3) if b else None


@dataclass
class TeamRow:
    """순위표의 팀 한 줄을 쌓아 가는 그릇."""

    code: str
    name: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    points: int = 0
    sets_won: int = 0
    sets_lost: int = 0
    points_won: int = 0
    points_lost: int = 0
    form: list = field(default_factory=list)  # 경기 결과 "W"/"L", 가장 최근이 맨 뒤

    def as_dict(self, rank):
        """API 응답 모양으로. 최근 폼은 마지막 5경기만."""
        return {"rank": rank, "team": {"code": self.code, "name": self.name}, "games": self.games,
                "wins": self.wins, "losses": self.losses, "points": self.points,
                "sets_won": self.sets_won, "sets_lost": self.sets_lost, "set_ratio": ratio(self.sets_won, self.sets_lost),
                "points_won": self.points_won, "points_lost": self.points_lost,
                "point_ratio": ratio(self.points_won, self.points_lost), "form": self.form[-5:]}


def _ratio_key(won, lost):
    """정렬용 득실률(잃은 게 없으면 무한대 = 가장 좋음)."""
    return float("inf") if not lost else won / lost


def team_standings(teams, matches):
    """순위표를 만든다. teams: [(코드, 이름)], matches: 날짜순 경기 행
    (home, away 팀 코드, home_sets, away_sets, home_points, away_points = 모든 세트의 랠리 점수 합)."""
    rows = {code: TeamRow(code, name) for code, name in teams}
    for m in matches:
        # each match counted once from each side: (team, sets won, sets lost, points won, points lost)
        for me, ws, ls, wp, lp in ((m["home"], m["home_sets"], m["away_sets"], m["home_points"], m["away_points"]),
                                   (m["away"], m["away_sets"], m["home_sets"], m["away_points"], m["home_points"])):
            r = rows.setdefault(me, TeamRow(me, me))
            r.games += 1
            won = ws > ls
            r.wins += won
            r.losses += not won
            r.points += match_points(ws, ls)
            r.sets_won += ws
            r.sets_lost += ls
            r.points_won += wp or 0
            r.points_lost += lp or 0
            r.form.append("W" if won else "L")
    ordered = sorted(rows.values(), key=lambda r: (-r.points, -r.wins, -_ratio_key(r.sets_won, r.sets_lost),
                                                   -_ratio_key(r.points_won, r.points_lost), r.name))
    out, prev, rank = [], None, 0
    for i, r in enumerate(ordered, 1):
        key = (r.points, r.wins, _ratio_key(r.sets_won, r.sets_lost), _ratio_key(r.points_won, r.points_lost))
        rank = rank if key == prev else i  # 모든 기준이 같은 팀은 같은 순위(1, 1, 3 식)
        prev = key
        out.append(r.as_dict(rank))
    return out


# ---------------------------------------------------------------- 선수 순위
# 순위 자격은 이 사이트의 규칙이다(KOVO 공식 기준은 수집 데이터에 없다): 팀 세트의 1/3 이상 출전해야 하고,
# 비율 부문(공격 성공률, 리시브 효율)은 시도 수도 충분해야 한다.
MIN_SET_SHARE = 1 / 3
CATEGORIES = {
    # 부문 키: (이름, 값 계산 함수, 단위, (시도 컬럼, 팀 세트당 최소 시도 수) 또는 None)
    "points": ("득점", lambda p: p["points"], "점", None),
    "attack": ("공격 성공률", lambda p: _pct(p["attack_success"], p["attack_attempts"]), "%", ("attack_attempts", 2.0)),
    "serve": ("서브 (세트당)", lambda p: _per_set(p["serve_aces"], p), "개", None),
    "block": ("블로킹 (세트당)", lambda p: _per_set(p["block_success"], p), "개", None),
    "dig": ("디그 (세트당)", lambda p: _per_set(p["dig_success"], p), "개", None),
    "set": ("세트 (세트당)", lambda p: _per_set(p["set_success"], p), "개", None),
    "receive": ("리시브 효율", lambda p: _pct(p["receive_excellent"] - p["receive_errors"], p["receive_attempts"]), "%",
                ("receive_attempts", 1.0)),
}


def _pct(a, b):
    """백분율(소수 둘째 자리). 분모가 0이면 None."""
    return round(100 * a / b, 2) if b else None


def _per_set(a, p):
    """선수가 뛴 세트당 값. 세트가 0이면 None."""
    return round(a / p["sets_played"], 3) if p["sets_played"] else None


def player_ranking(players, team_sets, category, limit=20):
    """한 부문의 선수 순위. players: 선수별 시즌 합계(code, name, team_code, team_name, sets_played, 박스스코어 합),
    team_sets: {팀 코드: 팀이 치른 세트 수}. 자격을 갖춘 선수 중 상위 `limit`명(동점은 같은 순위)."""
    label, value, unit, attempts_rule = CATEGORIES[category]
    rows = []
    for p in players:
        sets = team_sets.get(p["team_code"], 0)
        if category != "points" and p["sets_played"] < sets * MIN_SET_SHARE:
            continue
        if attempts_rule and p[attempts_rule[0]] < sets * attempts_rule[1]:
            continue
        v = value(p)
        if v is None or not p["games"] or (v <= 0 and category != "receive"):  # 리시브 효율은 음수일 수 있다
            continue
        rows.append((v, p))
    rows.sort(key=lambda x: (-x[0], x[1]["name"]))
    out, prev, rank = [], None, 0
    for i, (v, p) in enumerate(rows[:limit], 1):
        rank = rank if v == prev else i
        prev = v
        basis = {"attack": ("attack_success", "attack_attempts"), "receive": ("receive_excellent", "receive_attempts")}
        detail = {k: p[k] for k in basis.get(category, ())}  # 예: 249/500. 비율을 실제 시도 수와 함께 볼 수 있게
        out.append({"rank": rank, "player": {"code": p["code"], "name": p["name"]},
                    "team": {"code": p["team_code"], "name": p["team_name"]},
                    "value": v, "sets_played": p["sets_played"], "games": p["games"], **detail})
    return {"category": category, "label": label, "unit": unit, "rows": out}
