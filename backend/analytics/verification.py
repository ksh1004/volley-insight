"""예측 검증(ANL-10): 승패 예측 모델이 얼마나 잘 맞히는지를 누구나 확인할 수 있는 숫자로 보여 준다.

  backtest     최근 시즌들을 각각 '그 이전 시즌들로만 학습한 모델'로 예측한 결과(워크포워드,
               spark_jobs/train_model.py → prediction_backtest). 같은 경기의 경기 전 Elo 확률과 나란히 비교
  live         이번 시즌 끝난 경기에 대해 경기 전에 실제로 냈던 예측의 적중 여부(match_prediction.is_correct)
  calibration  백테스트를 '모델이 얼마나 확신했나' 구간으로 묶어, 예측한 확률과 실제 적중률을 비교(보정 곡선)
"""
import math

from league.models import Season
from matches.serializers import match_key, team_ref

from .models import MatchPrediction, ModelVersion, PredictionBacktest

EPS = 1e-6  # log(0) 방지
# 확신도 구간(이긴다고 본 쪽의 확률): 50~55%, 55~60%, ... 80% 이상
BINS = [(0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.01)]


def _metrics(pairs):
    """[(홈 승리 확률, 홈이 이겼나)] → 적중률, 로그 손실, Brier(낮을수록 좋음)."""
    n = len(pairs)
    if not n:
        return {"n": 0, "accuracy": None, "log_loss": None, "brier": None}
    acc = sum((p >= 0.5) == won for p, won in pairs) / n
    ll = -sum(math.log(min(max(p if won else 1 - p, EPS), 1 - EPS)) for p, won in pairs) / n
    brier = sum((p - won) ** 2 for p, won in pairs) / n
    return {"n": n, "accuracy": round(acc, 4), "log_loss": round(ll, 4), "brier": round(brier, 4)}


def calibration(pairs):
    """확신도(예측한 승자의 확률) 구간마다 → 평균 예측 확률과 실제 적중률. 잘 맞춰진 모델은 둘이 비슷하다."""
    out = []
    for lo, hi in BINS:
        rows = [(max(p, 1 - p), (p >= 0.5) == won) for p, won in pairs if lo <= max(p, 1 - p) < hi]
        if rows:
            out.append({"from": lo, "to": min(hi, 1.0), "n": len(rows),
                        "predicted": round(sum(c for c, _ in rows) / len(rows), 4),
                        "actual": round(sum(h for _, h in rows) / len(rows), 4)})
    return out


def summary(gender=None):
    """예측 검증 화면의 모든 숫자. 백테스트는 백테스트 결과가 있는 가장 최신 모델 버전 기준.
    gender를 주면 그 성별 경기만 센다."""
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
