"""Player form: the last N games against the earlier games of the same season (ANL-08).

A change counts as a trend only when it is larger than chance would explain (the difference divided by its
standard error reaches Z, about the one-sided 95% level) and large enough to matter (MIN_CHANGE, e.g. half a point
per set). Otherwise the form is "flat", which keeps a couple of lucky games from being called a rise.

  points per set     per-game values; standard error from their spread
  attack success     pooled success rates of the two periods (binomial)
  receive efficiency each reception scores +1 (excellent), -1 (error) or 0; mean and spread from the counts
"""
import math

N_RECENT = 5
MIN_BEFORE = 3
MIN_ATTEMPTS = 15
Z = 1.64


def _trend(z, change, min_change):
    if z is None:
        return None
    if abs(change) < min_change:
        return "flat"
    return "up" if z >= Z else "down" if z <= -Z else "flat"


def _round(v, d=2):
    return round(v, d) if v is not None else None


def _sum(rows, f):
    return sum(r.get(f) or 0 for r in rows)


def points_per_set(recent, before):
    xs_r = [r["points"] / r["sets"] for r in recent if r["sets"]]
    xs_b = [r["points"] / r["sets"] for r in before if r["sets"]]
    if len(xs_r) < 3 or len(xs_b) < MIN_BEFORE:
        return None
    allx = xs_r + xs_b
    mean = sum(allx) / len(allx)
    sd = math.sqrt(sum((x - mean) ** 2 for x in allx) / (len(allx) - 1)) if len(allx) > 1 else 0
    r, b = sum(xs_r) / len(xs_r), sum(xs_b) / len(xs_b)
    se = sd * math.sqrt(1 / len(xs_r) + 1 / len(xs_b))
    return r, b, ((r - b) / se) if se else None


def attack_rate(recent, before):
    a1, s1, a0, s0 = (_sum(recent, "attack_attempts"), _sum(recent, "attack_success"),
                      _sum(before, "attack_attempts"), _sum(before, "attack_success"))
    if a1 < MIN_ATTEMPTS or a0 < MIN_ATTEMPTS:
        return None
    p1, p0, p = s1 / a1, s0 / a0, (s1 + s0) / (a1 + a0)
    se = math.sqrt(p * (1 - p) * (1 / a1 + 1 / a0))
    return 100 * p1, 100 * p0, ((p1 - p0) / se) if se else None


def receive_efficiency(recent, before):
    def stats(rows):
        n, exc, err = _sum(rows, "receive_attempts"), _sum(rows, "receive_excellent"), _sum(rows, "receive_errors")
        if n < MIN_ATTEMPTS:
            return None
        mean = (exc - err) / n
        return n, mean, (exc + err) / n - mean ** 2

    r, b = stats(recent), stats(before)
    if r is None or b is None:
        return None
    se = math.sqrt(r[2] / r[0] + b[2] / b[0])
    return 100 * r[1], 100 * b[1], ((r[1] - b[1]) / se) if se else None


# key, label, function, decimals, smallest change that matters
METRICS = [("points_per_set", "세트당 득점", points_per_set, 2, 0.5),
           ("attack_pct", "공격 성공률", attack_rate, 1, 5.0),
           ("receive_efficiency", "리시브 효율", receive_efficiency, 1, 5.0)]


def player_form(games, n=N_RECENT):
    """games: the player's finished games of one season, oldest first, each with sets, points and box-score counts.
    Returns None when there are not enough games to compare."""
    played = [g for g in games if g.get("sets")]
    if len(played) < n + MIN_BEFORE:
        return None
    recent, before = played[-n:], played[:-n]
    metrics = []
    for key, label, fn, digits, min_change in METRICS:
        res = fn(recent, before)
        if res is None:
            continue
        r, b, z = res
        metrics.append({"key": key, "label": label, "recent": _round(r, digits), "before": _round(b, digits),
                        "change": _round(r - b, digits), "z": _round(z, 2), "trend": _trend(z, r - b, min_change)})
    if not metrics:
        return None
    trends = {m["trend"] for m in metrics}
    overall = "up" if "up" in trends and "down" not in trends else "down" if "down" in trends and "up" not in trends \
        else "mixed" if trends >= {"up", "down"} else "flat"
    return {"n": n, "games_before": len(before), "overall": overall, "metrics": metrics,
            "series": [{"match": g.get("match"), "date": g.get("date"), "points_per_set": _round(g["points"] / g["sets"])}
                       for g in played]}
