"""Team standings and player rankings (WEB-05), computed from finished matches.

Team order follows the V-League rule: match points (3:0/3:1 win 3, 3:2 win 2, 2:3 loss 1, other loss 0)
-> wins -> set ratio -> point ratio. Head-to-head, the last tie-breaker, is not applied.
Pure functions over plain rows so the rules can be tested without a database.
"""
from dataclasses import dataclass, field


def match_points(won_sets: int, lost_sets: int) -> int:
    if won_sets == 3:
        return 3 if lost_sets <= 1 else 2
    return 1 if (won_sets == 2 and lost_sets == 3) else 0


def ratio(a, b):
    """Won / lost; None when nothing was lost (shown as "MAX", sorted first)."""
    return round(a / b, 3) if b else None


@dataclass
class TeamRow:
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
    form: list = field(default_factory=list)  # "W"/"L", most recent last

    def as_dict(self, rank):
        return {"rank": rank, "team": {"code": self.code, "name": self.name}, "games": self.games,
                "wins": self.wins, "losses": self.losses, "points": self.points,
                "sets_won": self.sets_won, "sets_lost": self.sets_lost, "set_ratio": ratio(self.sets_won, self.sets_lost),
                "points_won": self.points_won, "points_lost": self.points_lost,
                "point_ratio": ratio(self.points_won, self.points_lost), "form": self.form[-5:]}


def _ratio_key(won, lost):
    return float("inf") if not lost else won / lost


def team_standings(teams, matches):
    """teams: [(code, name)], matches: rows ordered by date with keys
    home, away (codes), home_sets, away_sets, home_points, away_points (rally points over all sets)."""
    rows = {code: TeamRow(code, name) for code, name in teams}
    for m in matches:
        for me, op, ws, ls, wp, lp in ((m["home"], m["away"], m["home_sets"], m["away_sets"], m["home_points"], m["away_points"]),
                                       (m["away"], m["home"], m["away_sets"], m["home_sets"], m["away_points"], m["home_points"])):
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
        rank = rank if key == prev else i  # fully tied teams share a rank
        prev = key
        out.append(r.as_dict(rank))
    return out


# ---------------------------------------------------------------- player rankings
# Qualification is this site's rule (KOVO's official thresholds are not published in the data we collect):
# a player must have played at least a third of the team's sets; rate categories also need enough attempts.
MIN_SET_SHARE = 1 / 3
CATEGORIES = {
    # key: (label, value function, unit, minimum attempts per team set or None)
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
    return round(100 * a / b, 2) if b else None


def _per_set(a, p):
    return round(a / p["sets_played"], 3) if p["sets_played"] else None


def player_ranking(players, team_sets, category, limit=20):
    """players: per-player season totals with keys code, name, team_code, team_name, sets_played and box-score sums.
    team_sets: {team_code: sets played by the team}. Returns the top `limit` qualified players."""
    label, value, unit, attempts_rule = CATEGORIES[category]
    rows = []
    for p in players:
        sets = team_sets.get(p["team_code"], 0)
        if category != "points" and p["sets_played"] < sets * MIN_SET_SHARE:
            continue
        if attempts_rule and p[attempts_rule[0]] < sets * attempts_rule[1]:
            continue
        v = value(p)
        if v is None or not p["games"] or (v <= 0 and category != "receive"):  # receive efficiency can be negative
            continue
        rows.append((v, p))
    rows.sort(key=lambda x: (-x[0], x[1]["name"]))
    out, prev, rank = [], None, 0
    for i, (v, p) in enumerate(rows[:limit], 1):
        rank = rank if v == prev else i
        prev = v
        basis = {"attack": ("attack_success", "attack_attempts"), "receive": ("receive_excellent", "receive_attempts")}
        detail = {k: p[k] for k in basis.get(category, ())}  # e.g. 249/500, so a rate can be read in context
        out.append({"rank": rank, "player": {"code": p["code"], "name": p["name"]},
                    "team": {"code": p["team_code"], "name": p["team_name"]},
                    "value": v, "sets_played": p["sets_played"], "games": p["games"], **detail})
    return {"category": category, "label": label, "unit": unit, "rows": out}
