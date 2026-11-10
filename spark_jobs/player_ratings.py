"""포지션별 능력치(선수 카드): 선수-시즌마다 그 포지션에 중요한 능력을 1~99점으로 매기고 종합 점수를 낸다.
2026-11-09 데이터 분석을 거쳐 사용자와 합의한 방식이다(아래 숫자들이 그 분석 결과).

  HDFS /curated/kovo/{match,player_match_stat,player_attack_stat}  정규리그, 모든 시즌  → 박스스코어 합계
  HDFS /curated/kovo/rally_event (2023-24부터)                                          → 서브 압박,
                                                                                         세터의 추가 득점 기여
    → 성별 × 포지션 × 시즌마다: 기준 집단 = 그 시즌과 앞 두 시즌의 순위 자격 선수
    → 수치마다 기준 평균 쪽으로 당긴 값(시도가 적으면 평균에 가깝게, 축소 추정)의 기준 집단 안 z를 구한다
    → 능력치 = 그 능력에 속한 수치들의 z 평균을 다시 표준화, 점수 = 60 + 12 z, 1~99로 자름
       (60 = 그 포지션 순위 자격 선수의 평균, 72 = 표준편차 1만큼 위)
    → 종합 = 카드에 보이는 능력치 점수의 가중 평균(평균 60, 표준편차 약 8: 80 이상은 드물다)
    → 통산 카드(선수 × 포지션): 능력치마다 시즌 점수를 출전 세트 수로 가중 평균(시즌 점수가 이미 그 시기 선수들과
       비교한 값이라 시대가 달라도 같은 눈금), 근거 기록은 시즌 기록을 모두 합쳐 다시 계산
    → PostgreSQL player_rating, player_rating_career (매번 전체 교체, sql/merge_player_ratings.sql)
    → HDFS /marts/kovo/player_rating (시즌, 선수, 포지션, 종합): 경기 예측 모델의 squad 특징
       (build_features.py는 이전 시즌 카드만 쓴다)

  용어: z = (값 - 기준 평균) / 기준 표준편차. 축소 추정 = (성공 + 기준평균 × k) / (시도 + k), k는 '가상의 평균 시도 수'.

능력치 구성과 이유(2026-11-09 분석. "이적" = 팀을 옮긴 선수의 지난 시즌 값과의 상관. 그 수치가 팀이 아니라 선수를
따라다니는지 보는 가장 엄격한 시험):
  OH  리시브(리시브 정확률, 이적 0.55), 득점력(세트당 득점 0.51), 블로킹(세트당 블로킹 0.70),
      서브(서브 압박 / 서브당 (에이스-범실) 0.55), 공격(효율: 0.08뿐이라 크게 당김), 수비(세트당 디그 0.38)
  OP  득점력(0.78), 공격(0.52), 블로킹(0.70), 서브(서브 압박 / 세트당 에이스 0.49), 수비(0.58)
  MB  블로킹(0.41), 견제(블로킹 시도당 유효 블로킹 0.53. 세트당으로는 0.18뿐), 속공(0.30),
      서브(서브 압박 / 세트당 에이스 0.45)
  S   토스, 서브, 블로킹. KOVO '세트 성공' = 토스를 받은 공격수가 득점한 횟수, 즉 공격수의 숫자다: 이적한 세터의
      값은 세터 본인(0.15)보다 새 팀(0.30)을 따라갔다. 그래서 2023-24부터 토스 점수 = 그 세터의 토스로 낸 득점률
      - 같은 공격수가 다른 세터의 공을 받았을 때의 득점률. 그 전 시즌은 세트 성공률을 쓰고 '참고값'으로 표시한다.
  L   리시브(0.51), 디그(세트당 디그 + 디그 성공률), 연결(세트당 토스 시도: 두 번째 볼을 리베로가 얼마나 맡는지.
      그 토스의 질은 잴 수 없었다(다음 시즌 상관 0.02). 보여 주기만 하고 종합에 넣지 않음)
쓰지 않은 것(대부분 운. 다음 시즌 상관 0.3 이하): 오픈 공격 성공률, 디그 성공률 단독, 리베로 리시브 범실.

가중치는 그다음 결과와 맞춰 봤다(2026-11-09): 팀 자격 선수들의 종합 점수(세트 가중 평균)와 팀 승률의 상관,
234 팀-시즌(2008-09 ~ 2025-26). 처음엔 OH 25/20/15/15/15/10, OP 30/25/20/15/10, MB 35/20/25/20, L 50/35/15로
r 0.713(모두 같은 가중치면 0.663). 능력치 하나씩 승률과의 상관: 공격 0.73, 토스 0.63, 속공 0.55, 블로킹 0.48,
서브 0.31, 리시브 0.24, 득점력 0.20, 디그 0.10, OH/OP 수비 -0.04, MB 견제 -0.02, 리베로 연결 -0.18.
수비·견제·연결의 비중을 공격과 속공으로 옮기자(아래 값) r 0.738이 됐고, 역사의 앞뒤 절반 모두에서 좋아져
(0.772 → 0.795, 0.663 → 0.686) 한 시기에만 맞춘 결과가 아님을 확인했다.

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh player_ratings.py   (kovo_daily에도 포함)
"""
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_player_ratings.sql"
FINISHED = ["FINISHED", "CONFIRMED"]
REGULAR = "201"
PBP_FROM = "020"  # 문자중계는 2023-24(시즌 020)부터 있다
# 순위 자격(팀 세트의 1/3), 카드를 만드는 최소 세트, 이보다 적으면 '잠정' 표시
MIN_SET_SHARE, MIN_SETS, SURE_SETS = 1 / 3, 10, 30
WINDOW = 3  # 기준 집단 시즌 수: 그 시즌과 앞 두 시즌
MIN_REF = 8  # 기준 집단의 자격 선수가 이보다 적으면 점수를 내지 않는다(비교 대상이 너무 적음)
SCALE_MEAN, SCALE_SD, LOW, HIGH = 60, 12, 1, 99  # 점수 눈금: 평균 60, 표준편차 1당 12점, 1~99
ATTACKS = ["AA", "AC", "AT", "AE", "AQ", "AW", "AZ"]  # 공격 동작 코드들
SETTER_PRIOR = 20  # 세트(토스) 수: 공격수가 다른 세터의 공을 거의 안 받았으면 그의 시즌 득점률 쪽으로 기준을 당긴다
CAREER_MIN_QUALIFIED = 3  # 역대 순위에 들려면 그 포지션의 자격 시즌이 이만큼 필요(통산: 평균 59~60, 표준편차 5~8)
PERIODS = ("2023–24부터", "2022–23까지")  # 통산 근거 기록이 문자중계 시즌만 / 그 이전만 합친 값일 때 붙이는 기간

# 수치 → (분자, 분모, 당기는 무게 k, 당기는 방향): 비율은 (성공 + 기준평균 × k) / (시도 + k).
# 세트당 수치는 세트 수가 분모. 방향 "zero"는 0 쪽으로 당긴다(기대치와의 '차이'라서 평균이 0이므로)
METRICS = {
    "rec_exc": ("receive_excellent", "receive_attempts", 60, "mean"),
    "attack_eff": ("attack_net", "attack_attempts", 80, "mean"),
    "serve_net": ("serve_net", "serve_attempts", 60, "mean"),
    "serve_pressure": ("serve_hits", "serves", 80, "mean"),
    "quick_rate": ("quick_success", "quick_attempts", 40, "mean"),
    "block_touch_rate": ("block_effective", "block_attempts", 60, "mean"),
    "set_rate": ("set_success", "set_attempts", 100, "mean"),
    "setter_added": ("set_added", "pbp_sets", 300, "zero"),
    "dig_rate": ("dig_success", "dig_attempts", 40, "mean"),
    "points_ps": ("points", "sets", 10, "mean"),
    "block_ps": ("block_success", "sets", 10, "mean"),
    "ace_ps": ("serve_aces", "sets", 10, "mean"),
    "dig_ps": ("dig_success", "sets", 10, "mean"),
    "set_att_ps": ("set_attempts", "sets", 10, "mean"),
}

# 포지션 → [(키, 이름, 가중치 %, 대안들)]: 대안 하나는 능력치로 평균 낼 수치 목록이다.
# 선수에게 값이 있는 첫 번째 대안을 쓴다(문자중계 버전이 먼저, 없으면 박스스코어 버전)
SERVE_OH = [["serve_pressure"], ["serve_net"]]
SERVE = [["serve_pressure"], ["ace_ps"]]
ABILITIES = {
    "OH": [("receive", "리시브", 25, [["rec_exc"]]), ("scoring", "득점력", 20, [["points_ps"]]),
           ("block", "블로킹", 15, [["block_ps"]]), ("serve", "서브", 15, SERVE_OH),
           ("attack", "공격", 20, [["attack_eff"]]), ("defense", "수비", 5, [["dig_ps"]])],
    "OP": [("scoring", "득점력", 25, [["points_ps"]]), ("attack", "공격", 30, [["attack_eff"]]),
           ("block", "블로킹", 20, [["block_ps"]]), ("serve", "서브", 15, SERVE), ("defense", "수비", 10, [["dig_ps"]])],
    "MB": [("block", "블로킹", 35, [["block_ps"]]), ("touch", "견제", 10, [["block_touch_rate"]]),
           ("quick", "속공", 35, [["quick_rate"]]), ("serve", "서브", 20, SERVE)],
    "S": [("setting", "토스", 50, [["setter_added"], ["set_rate"]]), ("serve", "서브", 25, SERVE),
          ("block", "블로킹", 25, [["block_ps"]])],
    # 연결은 카드에 보이지만 종합에 넣지 않는다: 리베로 토스가 많을수록 오히려 팀이 졌다(팀 상관 -0.18)
    "L": [("receive", "리시브", 60, [["rec_exc"]]), ("dig", "디그", 40, [["dig_ps", "dig_rate"]]),
          ("linking", "연결", 0, [["set_att_ps"]])],
}
# 카드의 근거 기록 줄에 보여 줄 이름과 단위(당기지 않은 원래 값을 보여 준다). pct %, pp %p, n2 소수 둘째 자리
SHOW = {
    "rec_exc": ("리시브 정확", "pct"), "attack_eff": ("공격 효율", "pct"), "serve_net": ("서브 (에이스-범실)/시도", "pct"),
    "serve_pressure": ("서브 압박 (에이스·흔든 리시브)", "pct"), "quick_rate": ("속공 성공률", "pct"),
    "block_touch_rate": ("블로킹 시도당 유효 블로킹", "pct"), "set_rate": ("세트 성공률", "pct"),
    "setter_added": ("같은 공격수 대비 득점률", "pp"), "dig_rate": ("디그 성공률", "pct"),
    "points_ps": ("세트당 득점", "n2"), "block_ps": ("세트당 블로킹", "n2"), "ace_ps": ("세트당 서브 에이스", "n2"),
    "dig_ps": ("세트당 디그", "n2"), "set_att_ps": ("세트당 토스 시도", "n2"),
}
NOTES = {"set_rate": "공격수 영향이 큰 참고값 (문자중계가 없는 시즌)"}  # 능력치 옆에 붙는 주의 문구
CAREER_NOTES = {"set_rate": "세트 성공률로 계산한 시즌은 공격수 영향이 큰 참고값"}  # 통산에 문자중계 시즌이 섞일 때


# ---------------------------------------------------------------- 순수 함수(spark_jobs/tests에서 테스트)
def to_score(z):
    """z → 점수(60 + 12z, 반올림, 1~99로 자름). z가 없으면 None."""
    return None if z is None else max(LOW, min(HIGH, round(SCALE_MEAN + SCALE_SD * z)))


def raw_value(row, metric):
    """당기지 않은 원래 값(분자 / 분모). 분모가 0이거나 없으면 None."""
    num, den, _, _ = METRICS[metric]
    d = row.get(den)
    return None if not d or row.get(num) is None else row[num] / d


def shrunk_value(row, metric, prior_mean):
    """기준 평균(또는 0) 쪽으로 당긴 값: (분자 + 중심 × k) / (분모 + k). 시도가 적을수록 중심에 가깝다."""
    num, den, k, prior = METRICS[metric]
    d = row.get(den)
    if d is None or row.get(num) is None:
        return None
    centre = 0.0 if prior == "zero" else prior_mean
    return (row[num] + centre * k) / (d + k)


def pooled_mean(rows, metric):
    """기준 집단 전체의 합친 평균(전체 분자 합 / 전체 분모 합). 'zero' 수치는 0."""
    num, den, _, prior = METRICS[metric]
    if prior == "zero":
        return 0.0
    made = sum(r[num] for r in rows if r.get(den) and r.get(num) is not None)
    att = sum(r[den] for r in rows if r.get(den) and r.get(num) is not None)
    return made / att if att else None


def standardise(values, reference):
    """각 값의 z를 기준 값들의 평균·모표준편차로 구한다. 값이 없거나 기준이 MIN_REF보다 적으면 None."""
    ref = [v for v in reference if v is not None]
    if len(ref) < MIN_REF:
        return [None] * len(values)
    mu, sd = statistics.mean(ref), statistics.pstdev(ref)
    return [None if v is None or not sd else (v - mu) / sd for v in values]


def score_group(group, targets, position):
    """`targets`(한 시즌의 행들)를 `group`(같은 성별·포지션, 기준 시즌들의 행들)과 비교해 점수를 매긴다.
    행은 합계가 담긴 딕셔너리이고 `qualified`가 참인 행이 기준이 된다. {id(행): 결과}를 돌려준다.
    순서: 수치별 z → 대안별로 능력치 z(수치 z 평균을 다시 표준화) → 선수마다 값이 있는 첫 대안 선택 → 점수·종합."""
    rows = list({id(r): r for r in group + targets}.values())
    in_group = {id(r) for r in group}
    ref = [r for r in rows if r["qualified"] and id(r) in in_group]
    zs = {}  # 수치 → {id(행): z}
    for metric in {m for _, _, _, alts in ABILITIES[position] for alt in alts for m in alt}:
        has = [r for r in ref if r.get(METRICS[metric][1])]
        mean = pooled_mean(has, metric)
        if mean is None or len(has) < MIN_REF:
            continue
        vals = [shrunk_value(r, metric, mean) for r in rows]
        refvals = [shrunk_value(r, metric, mean) for r in has]
        zs[metric] = {id(r): z for r, z in zip(rows, standardise(vals, refvals))}

    ability_z = defaultdict(dict)  # 능력치 키 → {id(행): z}
    chosen = defaultdict(dict)  # 능력치 키 → {id(행): 실제로 쓴 수치들}(카드의 근거 기록 줄에 씀)
    for key, _, _, alts in ABILITIES[position]:
        per_alt = []
        for alt in alts:
            if not all(m in zs for m in alt):
                per_alt.append({})
                continue
            raw = {}
            for r in rows:
                parts = [zs[m].get(id(r)) for m in alt]
                raw[id(r)] = statistics.mean(parts) if all(p is not None for p in parts) else None
            # 여러 수치를 평균 낸 능력치는 표준편차가 1보다 작아지므로, 기준 행들로 다시 표준화한다
            ids = list(raw)
            z = standardise([raw[i] for i in ids], [raw[id(r)] for r in ref])
            per_alt.append(dict(zip(ids, z)))
        for r in rows:
            for alt, z in zip(alts, per_alt):
                if z.get(id(r)) is not None:
                    ability_z[key][id(r)] = z[id(r)]
                    chosen[key][id(r)] = alt
                    break

    out = {}
    for r in targets:
        abilities = []
        for key, label, weight, _ in ABILITIES[position]:
            z = ability_z[key].get(id(r))
            used = chosen[key].get(id(r), [])
            stats = [{"metric": m, "label": SHOW[m][0], "unit": SHOW[m][1], "value": raw_value(r, m)} for m in used]
            note = next((NOTES[m] for m in used if m in NOTES), "")
            abilities.append({"key": key, "label": label, "weight": weight, "score": to_score(z),
                              "z": None if z is None else round(z, 3), "stats": stats, "note": note})
        out[id(r)] = {"overall": overall_score(abilities), "abilities": abilities}
    return out


def overall_score(abilities):
    """카드에 보이는 능력치 점수의 가중 평균(2026-11-09 합의: 누구나 카드만 보고 계산해 볼 수 있게).
    가중치가 있는 능력치 중 점수 없는 것이 둘 이상이면 None. 가중치 0인 능력치는 보여 주기만 하고 계산에서 뺀다."""
    weighted = [a for a in abilities if a["weight"]]
    parts = [(a["weight"], a["score"]) for a in weighted if a["score"] is not None]
    if not parts or len(parts) < len(weighted) - 1:
        return None
    return round(sum(w * s for w, s in parts) / sum(w for w, _ in parts))


def career_cards(scored):
    """점수가 매겨진 시즌들([(행, {"overall", "abilities"})])로 선수 × 포지션마다 통산 카드 하나를 만든다.
    능력치 = 시즌 점수를 출전 세트로 가중 평균(점수가 없는 시즌은 빼고). 근거 기록 = 수치마다 그 수치를 쓴 시즌들의
    기록을 합쳐 다시 계산하고, 경력의 일부 기간에만 해당하면(문자중계 수치) 기간을 붙인다.
    종합 = 시즌 카드와 같은 방식의 가중 평균. 최고 시즌 = 자격 시즌 중 종합이 가장 높은 시즌(자격 시즌이 없으면 전체 중)."""
    by = defaultdict(list)
    for r, s in scored:
        by[(r["player_code"], r["gender"], r["position"])].append((r, s))
    cards = []
    for (player, gender, position), seasons in by.items():
        seasons.sort(key=lambda x: x[0]["season_code"])
        abilities = []
        for i, (key, label, weight, _) in enumerate(ABILITIES[position]):
            parts = [(r, s["abilities"][i]) for r, s in seasons if s["abilities"][i]["score"] is not None]
            sets = sum(r["sets"] for r, _ in parts)
            score = round(sum(r["sets"] * a["score"] for r, a in parts) / sets) if sets else None
            used = {}  # 수치 → 그 수치를 쓴 시즌들의 행(처음 쓴 순서대로)
            for r, a in parts:
                for st in a["stats"]:
                    used.setdefault(st["metric"], []).append(r)
            stats = []
            for metric, rows in used.items():
                num, den, _, _ = METRICS[metric]
                rows = [r for r in rows if r.get(den) and r.get(num) is not None]
                total = sum(r[den] for r in rows)
                stat = {"metric": metric, "label": SHOW[metric][0], "unit": SHOW[metric][1],
                        "value": sum(r[num] for r in rows) / total if total else None}
                codes = [r["season_code"] for r in rows]
                if len(codes) < len(parts) and codes:
                    if min(codes) >= PBP_FROM:
                        stat["period"] = PERIODS[0]
                    elif max(codes) < PBP_FROM:
                        stat["period"] = PERIODS[1]
                stats.append(stat)
            periods = {st["metric"]: st.get("period") for st in stats}
            note = next(((CAREER_NOTES if periods[m] else NOTES)[m] for m in used if m in NOTES), "")
            abilities.append({"key": key, "label": label, "weight": weight, "score": score, "z": None,
                              "stats": stats, "note": note})
        rated = [(r, s) for r, s in seasons if s["overall"] is not None]
        best = max([x for x in rated if x[0]["qualified"]] or rated, key=lambda x: x[1]["overall"], default=None)
        qualified_seasons = sum(1 for r, _ in seasons if r["qualified"])
        extra = {}
        if position == "S":
            extra["set_success_ps"] = round(sum(r["set_success"] for r, _ in seasons)
                                            / sum(r["sets"] for r, _ in seasons), 3)
        cards.append({"player_code": player, "gender": gender, "position": position, "seasons": len(seasons),
                      "qualified_seasons": qualified_seasons, "sets": sum(r["sets"] for r, _ in seasons),
                      "first_season": seasons[0][0]["season_code"], "last_season": seasons[-1][0]["season_code"],
                      "best_season": best[0]["season_code"] if best else None,
                      "best_overall": best[1]["overall"] if best else None,
                      "qualified": qualified_seasons >= CAREER_MIN_QUALIFIED,
                      "overall": overall_score(abilities), "abilities": abilities, "extra": extra})
    return cards


def score_all(rows):
    """모든 행(선수-시즌)을 성별 × 포지션 × 기준 시즌 묶음과 비교해 점수를 매긴다. [(행, 결과)] 목록."""
    by = defaultdict(list)
    for r in rows:
        if r["position"] in ABILITIES:
            by[(r["gender"], r["position"])].append(r)
    results = []
    for (_, position), group in by.items():
        seasons = sorted({r["season_code"] for r in group})
        for season in seasons:
            lo = f"{int(season) - WINDOW + 1:03d}"  # 기준 시작 시즌(예: "023"이면 "021")
            ref = [r for r in group if lo <= r["season_code"] <= season]
            targets = [r for r in group if r["season_code"] == season]
            scored = score_group(ref, targets, position)
            for r in targets:
                results.append((r, scored[id(r)]))
    return results


def setter_added(set_rows, setters):
    """세터의 추가 득점 기여 = 그 세터 토스에서 나온 득점 - 같은 공격수들이 그 시즌 다른 세터의 토스에서 낸 득점률 × 토스 수.
    set_rows: [(시즌, 세터, 공격수, 토스 수, 득점)], setters: 포지션이 S인 {(시즌, 선수)}.
    {(시즌, 세터): (추가 득점, 토스 수)}를 돌려준다. 다른 세터의 공을 거의 안 받은 공격수는 기준이 자기 시즌 득점률
    쪽으로 당겨져, 그 공격은 기여가 거의 0이 된다(누구 덕인지 구별할 수 없으므로)."""
    by_attacker = defaultdict(lambda: [0, 0])  # (시즌, 공격수) → [득점, 토스 수]: 받은 모든 토스
    by_setter_s = defaultdict(lambda: [0, 0])  # 같은 키, 포지션 S 선수가 올린 토스만
    for season, setter, attacker, n, k in set_rows:
        by_attacker[(season, attacker)][0] += k
        by_attacker[(season, attacker)][1] += n
        if (season, setter) in setters:
            by_setter_s[(season, attacker)][0] += k
            by_setter_s[(season, attacker)][1] += n
    out = defaultdict(lambda: [0.0, 0])
    for season, setter, attacker, n, k in set_rows:
        if (season, setter) not in setters:
            continue
        ak, an = by_attacker[(season, attacker)]
        own_rate = ak / an if an else 0.0
        sk, sn = by_setter_s[(season, attacker)]
        other_k, other_n = sk - k, sn - n  # 이 세터를 뺀 다른 세터들의 토스
        base = (other_k + own_rate * SETTER_PRIOR) / (other_n + SETTER_PRIOR)
        out[(season, setter)][0] += k - base * n
        out[(season, setter)][1] += n
    return {key: (v[0], v[1]) for key, v in out.items()}


# ---------------------------------------------------------------- Spark: 선수-시즌별 합계
def player_counts(spark):
    """박스스코어로 선수-시즌별 합계를 만든다(정규리그, 10세트 이상). 주 소속 팀, 최빈 포지션, 순위 자격,
    속공·이동 공격 시도·성공도 함께."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR))
         .select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets", "away_sets"))
    p = read("player_match_stat").join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    p = p.withColumn("sets", F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", "")))

    by_team = p.groupBy("season_code", "player_code", "team_code").agg(F.sum("sets").alias("team_sets_played"))
    w = Window.partitionBy("season_code", "player_code").orderBy(F.desc("team_sets_played"), "team_code")
    main_team = by_team.withColumn("_n", F.row_number().over(w)).where("_n = 1").select("season_code", "player_code", "team_code")
    sums = ["points", "attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_attempts",
            "serve_aces", "serve_errors", "block_attempts", "block_success", "block_effective", "receive_attempts",
            "receive_excellent", "dig_attempts", "dig_success", "set_attempts", "set_success"]
    agg = p.groupBy("season_code", "player_code", "gender").agg(
        F.sum("sets").alias("sets"), F.mode(F.when(F.col("position") != "", F.col("position"))).alias("position"),
        *[F.sum(c).alias(c) for c in sums])
    team_sets = m.select("season_code", F.explode(F.array("home_code", "away_code")).alias("team_code"),
                         (F.col("home_sets") + F.col("away_sets")).alias("s")) \
        .groupBy("season_code", "team_code").agg(F.sum("s").alias("team_sets"))
    agg = agg.join(main_team, ["season_code", "player_code"]).join(team_sets, ["season_code", "team_code"]) \
        .where(F.col("sets") >= MIN_SETS) \
        .withColumn("qualified", F.col("sets") >= F.col("team_sets") * MIN_SET_SHARE)

    quick = (read("player_attack_stat").join(m.select("season_code", "game_no"), ["season_code", "game_no"])
             .where(F.col("attack_type").isin("QUICK", "MOVING"))
             .groupBy("season_code", "player_code")
             .agg(F.sum("attempts").alias("quick_attempts"), F.sum("success").alias("quick_success")))
    return agg.join(quick, ["season_code", "player_code"], "left")


def play_by_play(spark):
    """문자중계로 선수-시즌별 값을 만든다: 서브 수와 '흔든 서브' 수(에이스, 또는 상대 리시브가 '정확'이 아님),
    그리고 (세터 → 바로 다음 같은 팀 공격수) 쌍별 토스 수와 득점."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR)
                             & (F.col("season_code") >= PBP_FROM)).select("season_code", "game_no"))
    e = read("rally_event").join(m, ["season_code", "game_no"])
    w = Window.partitionBy("season_code", "game_no", "set_no").orderBy("event_no")
    e = e.select("season_code", "team_code", "player_code", "action_code", "result_code",
                 F.lead("action_code").over(w).alias("na"), F.lead("result_code").over(w).alias("nr"),
                 F.lead("team_code").over(w).alias("nt"), F.lead("player_code").over(w).alias("np"))
    serves = (e.where(F.col("action_code").isin("VV", "VS") & F.col("player_code").isNotNull())
              .withColumn("hit", ((F.col("result_code") == "suc")
                                  | ((F.col("na") == "RR") & (F.col("nt") != F.col("team_code")) & (F.col("nr") != "exc")))
                          .cast("int"))
              .groupBy("season_code", "player_code").agg(F.count("*").alias("serves"), F.sum("hit").alias("serve_hits")))
    sets = (e.where((F.col("action_code") == "SS") & F.col("na").isin(ATTACKS) & (F.col("nt") == F.col("team_code"))
                    & F.col("player_code").isNotNull() & F.col("np").isNotNull())
            .groupBy("season_code", F.col("player_code").alias("setter"), F.col("np").alias("attacker"))
            .agg(F.count("*").alias("n"), F.sum((F.col("nr") == "suc").cast("int")).alias("kills")))
    return serves, sets


def build_rows(counts, serves, set_pairs):
    """Spark 결과(Row)들을 점수 계산용 딕셔너리 행으로 합친다. 파생 값(공격 순득점, 서브 순득점, 세터 기여)을 붙인다."""
    from player_types import normalize_position

    serve_by = {(r.season_code, r.player_code): (r.serves, r.serve_hits) for r in serves}
    rows = []
    for r in counts:
        d = r.asDict()
        d["position"] = normalize_position(d["season_code"], d["position"] or "")
        d["attack_net"] = d["attack_success"] - d["attack_blocked"] - d["attack_errors"]
        d["serve_net"] = d["serve_aces"] - d["serve_errors"]
        for c in ("quick_attempts", "quick_success"):
            d[c] = d[c] or 0
        sv = serve_by.get((d["season_code"], d["player_code"]))
        d["serves"], d["serve_hits"] = sv if sv and sv[0] >= 1 else (None, None)
        rows.append(d)
    setters = {(r["season_code"], r["player_code"]) for r in rows if r["position"] == "S"}
    added = setter_added([(s.season_code, s.setter, s.attacker, s.n, s.kills) for s in set_pairs], setters)
    for r in rows:
        a = added.get((r["season_code"], r["player_code"]))
        r["set_added"], r["pbp_sets"] = a if a else (None, None)
    return rows


def main():
    """합계 계산 → 점수 → 통산 카드 → (HDFS 마트, PostgreSQL 병합)."""
    from pyspark.sql import SparkSession

    from pg_merge import load_and_merge

    spark = (SparkSession.builder.appName("player-ratings")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    counts = player_counts(spark).collect()
    serves, set_pairs = play_by_play(spark)
    rows = build_rows(counts, serves.collect(), set_pairs.collect())
    scored = score_all(rows)
    out = []
    for r, s in scored:
        extra = {"set_success_ps": round(r["set_success"] / r["sets"], 3)} if r["position"] == "S" else {}
        out.append((r["season_code"], r["player_code"], r["team_code"], r["gender"], r["position"], int(r["sets"]),
                    bool(r["qualified"]), (not r["qualified"]) or r["sets"] < SURE_SETS, s["overall"],
                    json.dumps(s["abilities"], ensure_ascii=False), json.dumps(extra)))
    print(f"scored {len(out)} player-seasons")
    for pos in ABILITIES:
        n = sum(1 for o in out if o[4] == pos and o[8] is not None)
        print(f"  {pos}: {n} with an overall score")
    df = spark.createDataFrame(out, "season_code string, player_code string, team_code string, gender string, "
                                    "position string, sets int, qualified boolean, provisional boolean, overall int, "
                                    "abilities string, extra string")
    careers = [(c["player_code"], c["gender"], c["position"], c["seasons"], c["qualified_seasons"], int(c["sets"]),
                c["first_season"], c["last_season"], c["best_season"], c["best_overall"], c["qualified"], c["overall"],
                json.dumps(c["abilities"], ensure_ascii=False), json.dumps(c["extra"])) for c in career_cards(scored)]
    print(f"career cards {len(careers)} ({sum(c[10] for c in careers)} ranked all-time)")
    career_df = spark.createDataFrame(careers, "player_code string, gender string, position string, seasons int, "
                                               "qualified_seasons int, sets int, first_season string, "
                                               "last_season string, best_season string, best_overall int, "
                                               "qualified boolean, overall int, abilities string, extra string")
    df.select("season_code", "player_code", "team_code", "gender", "position", "sets", "qualified", "overall") \
        .write.mode("overwrite").parquet(f"{MARTS}/player_rating")
    tables = {"player_rating": df, "player_rating_career": career_df}
    for row in load_and_merge(spark, tables, MERGE_SQL, "merge_player_ratings"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
