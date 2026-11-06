"""Prediction verification (ANL-10): how good the match-winner model is, in numbers anyone can check.

  backtest   each of the last seasons predicted by a model trained only on the seasons before it (walk-forward,
             spark_jobs/train_model.py -> prediction_backtest), next to the pre-match Elo probability
  live       pre-match predictions of this season's games that are over (match_prediction.is_correct)
  calibration  of the backtest: grouped by how sure the model was, how often it was right
"""
import math

from league.models import Season
from matches.serializers import match_key, team_ref

from .models import MatchPrediction, ModelVersion, PredictionBacktest

EPS = 1e-6
BINS = [(0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.01)]


def _metrics(pairs):
    """pairs: [(home win probability, home won)] -> accuracy, log loss, Brier."""
    n = len(pairs)
    if not n:
        return {"n": 0, "accuracy": None, "log_loss": None, "brier": None}
    acc = sum((p >= 0.5) == won for p, won in pairs) / n
    ll = -sum(math.log(min(max(p if won else 1 - p, EPS), 1 - EPS)) for p, won in pairs) / n
    brier = sum((p - won) ** 2 for p, won in pairs) / n
    return {"n": n, "accuracy": round(acc, 4), "log_loss": round(ll, 4), "brier": round(brier, 4)}


def calibration(pairs):
    """Confidence (probability of the predicted winner) in bins -> predicted vs actual hit rate."""
    out = []
    for lo, hi in BINS:
        rows = [(max(p, 1 - p), (p >= 0.5) == won) for p, won in pairs if lo <= max(p, 1 - p) < hi]
        if rows:
            out.append({"from": lo, "to": min(hi, 1.0), "n": len(rows),
                        "predicted": round(sum(c for c, _ in rows) / len(rows), 4),
                        "actual": round(sum(h for _, h in rows) / len(rows), 4)})
    return out


def summary(gender=None):
    version = (ModelVersion.objects.filter(name="match-winner", backtests__isnull=False).order_by("-version").first()
               or ModelVersion.objects.filter(name="match-winner").order_by("-version").first())
    champion = ModelVersion.objects.filter(name="match-winner", alias="champion").first()
    out = {"model": None, "backtest": None, "live": None}
    if version:
        out["model"] = {"version": version.version, "is_champion": champion is not None and champion.id == version.id,
                        "champion_version": champion.version if champion else None,
                        "train_seasons": version.train_seasons, "test_season": version.test_season}

    bt = PredictionBacktest.objects.filter(model_version=version).select_related("match__competition__season")
    if gender:
        bt = bt.filter(match__gender=gender)
    rows = list(bt.values_list("match__competition__season__code", "home_win_prob", "elo_home_win_prob", "home_won"))
    if rows:
        years = dict(Season.objects.values_list("code", "years"))
        seasons = []
        for code in sorted({r[0] for r in rows}):
            rs = [r for r in rows if r[0] == code]
            seasons.append({"season": {"code": code, "years": years.get(code, "")},
                            "model": _metrics([(float(r[1]), r[3]) for r in rs]),
                            "elo": _metrics([(float(r[2]), r[3]) for r in rs]),
                            "train_seasons": bt.filter(match__competition__season__code=code)
                            .values_list("train_seasons", flat=True).first()})
        model_pairs = [(float(r[1]), r[3]) for r in rows]
        out["backtest"] = {"seasons": seasons, "overall": {"model": _metrics(model_pairs),
                                                           "elo": _metrics([(float(r[2]), r[3]) for r in rows])},
                           "calibration": calibration(model_pairs)}

    live = (MatchPrediction.objects.filter(is_correct__isnull=False)
            .select_related("match__competition__season", "match__home_team", "match__away_team", "model_version")
            .order_by("-match__scheduled_at"))
    if gender:
        live = live.filter(match__gender=gender)
    live = list(live)
    by_version = {}
    for p in live:
        by_version.setdefault(p.model_version.version, []).append((float(p.home_win_prob), p.match.home_sets > p.match.away_sets))
    out["live"] = {
        "overall": _metrics([(float(p.home_win_prob), p.match.home_sets > p.match.away_sets) for p in live]),
        "by_version": [{"version": v, **_metrics(pairs)} for v, pairs in sorted(by_version.items(), reverse=True)],
        "recent": [{"match": match_key(p.match), "date": p.match.scheduled_at, "gender": p.match.gender,
                    "home": team_ref(p.match.home_team), "away": team_ref(p.match.away_team),
                    "home_win_prob": float(p.home_win_prob), "pred_sets": [p.pred_home_sets, p.pred_away_sets],
                    "sets": [p.match.home_sets, p.match.away_sets], "is_correct": p.is_correct,
                    "version": p.model_version.version} for p in live[:20]],
    }
    return out
