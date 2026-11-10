"""선수 폼: 같은 시즌에서 최근 N경기를 그 전 경기들과 비교한다(ANL-08).

변화는 우연으로 설명하기 어려울 만큼 크고(차이 ÷ 표준오차가 Z, 대략 단측 95% 수준에 닿음), 동시에 의미 있을 만큼
클 때(MIN_CHANGE, 예: 세트당 0.5점)만 '상승/하락'으로 본다. 아니면 '유지'(flat)다. 운 좋은 경기 두어 번을
상승세로 부르지 않기 위한 규칙이다.

  세트당 득점     경기마다의 값. 표준오차는 그 값들의 흩어짐으로
  공격 성공률     두 기간의 합친 성공률(이항 분포)
  리시브 효율     리시브 하나가 +1(정확), -1(실패), 0 점수. 평균과 흩어짐을 개수로 계산
"""
import math

N_RECENT = 5  # 최근 몇 경기를 볼지
MIN_BEFORE = 3  # 비교할 이전 경기가 최소 몇 개 있어야 하는지
MIN_ATTEMPTS = 15  # 비율 지표는 각 기간에 시도가 이만큼은 있어야 비교한다
Z = 1.64  # 단측 95% 기준값


def _trend(z, change, min_change):
    """z와 변화량으로 up/down/flat을 정한다(z가 없으면 None)."""
    if z is None:
        return None
    if abs(change) < min_change:
        return "flat"
    return "up" if z >= Z else "down" if z <= -Z else "flat"


def _round(v, d=2):
    """None을 그대로 두는 반올림."""
    return round(v, d) if v is not None else None


def _sum(rows, f):
    """경기 행들에서 필드 f의 합(빈 값은 0)."""
    return sum(r.get(f) or 0 for r in rows)


def points_per_set(recent, before):
    """세트당 득점: (최근 평균, 이전 평균, z). 경기 수가 모자라면 None. 두 집단 평균 차이의 t 검정과 같은 방식."""
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
    """공격 성공률(%): (최근, 이전, z). 두 비율 차이의 z 검정(합친 비율로 표준오차)."""
    a1, s1, a0, s0 = (_sum(recent, "attack_attempts"), _sum(recent, "attack_success"),
                      _sum(before, "attack_attempts"), _sum(before, "attack_success"))
    if a1 < MIN_ATTEMPTS or a0 < MIN_ATTEMPTS:
        return None
    p1, p0, p = s1 / a1, s0 / a0, (s1 + s0) / (a1 + a0)
    se = math.sqrt(p * (1 - p) * (1 / a1 + 1 / a0))
    return 100 * p1, 100 * p0, ((p1 - p0) / se) if se else None


def receive_efficiency(recent, before):
    """리시브 효율(%): (최근, 이전, z). 리시브 하나를 +1/-1/0으로 본 평균의 차이 검정."""
    def stats(rows):
        """(시도 수, 평균, 분산). 시도가 모자라면 None."""
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


# (키, 화면 이름, 계산 함수, 소수 자릿수, 의미 있는 최소 변화량)
METRICS = [("points_per_set", "세트당 득점", points_per_set, 2, 0.5),
           ("attack_pct", "공격 성공률", attack_rate, 1, 5.0),
           ("receive_efficiency", "리시브 효율", receive_efficiency, 1, 5.0)]


def player_form(games, n=N_RECENT):
    """한 시즌의 끝난 경기들(오래된 순, 경기마다 세트·득점·박스스코어)로 폼을 판정한다. 비교할 경기가 모자라면 None.
    overall: 지표들이 모두 오르면 up, 모두 내리면 down, 섞이면 mixed, 아니면 flat. series는 그래프용 경기별 세트당 득점."""
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
