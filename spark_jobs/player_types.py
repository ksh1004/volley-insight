"""선수 유형과 비슷한 선수(ANL-06, WEB-07): 세트당 기록으로 Spark MLlib K-means 군집화를 한다.

  HDFS /curated/kovo/{match,player_match_stat,player_attack_stat}   정규리그, 모든 시즌
    → 선수-시즌마다 한 행: 플레이 스타일 특징 13개(세트당 기록량, 공격 종류별 양, 비율 2개)
    → 성별마다: 표준화 → k = 4..9로 K-means → 실루엣 점수가 최고의 90% 이상인 k 중 가장 큰 k를 고른다
       (비슷하게 잘 나뉘는 한 더 많고 세밀한 유형을 택함). 모든 k의 점수는 MLflow `player-types`에 남긴다
    → 각 그룹에 이름 붙이기: 주 포지션 + 같은 포지션의 다른 그룹과 비교해 두드러지는 점
    → 비슷한 선수: 표준화한 특징 사이의 거리가 가까운 선수. 같은 시즌 안에서, 그리고 역대 전체에서
    → PostgreSQL player_type, player_style (매번 전체 교체, sql/merge_player_types.sql)

  용어: K-means = 데이터를 k개 무리로 나누는 군집화, 실루엣 = 무리가 얼마나 잘 나뉘었는지(-1~1, 클수록 좋음),
        표준화(z) = (값 - 평균) / 표준편차. 단위가 다른 특징을 같은 눈금으로 비교하기 위해.

대상: 팀 세트의 1/3 이상(순위 자격 규칙) + 10세트 이상 출전한 선수. 그룹은 30세트 이상 선수로만 학습해 몇 세트
뛴 선수가 그룹 모양을 흔들지 않게 한다(그보다 적으면 가장 가까운 그룹에 배정하고 '잠정'으로 표시).
포지션은 정제 데이터 값을 쓴다(정제 단계에서 이미 모든 시즌이 현재 코드 체계로 바뀌어 있음).

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh player_types.py   (kovo_daily에도 포함)
"""
import json
import os
from pathlib import Path

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_player_types.sql"
EXPERIMENT = "player-types"
FINISHED = ["FINISHED", "CONFIRMED"]
REGULAR = "201"
MIN_SET_SHARE, MIN_SETS, FIT_SETS = 1 / 3, 10, 30  # 자격(팀 세트 비율), 최소 세트, 학습에 쓰는 최소 세트
PRIOR_ATTEMPTS = 20  # 비율은 '평균 시도 20번'을 더한 것처럼 평균 쪽으로 당긴다(시도가 적으면 평균에 가깝게)
K_RANGE = range(4, 10)  # 시험해 볼 그룹 수 k
SIMILAR_N = 5  # 비슷한 선수를 몇 명 보여 줄지
SEED = 7  # 난수 고정(실행마다 같은 결과)

# 특징 → (높을 때 붙일 이름, 낮을 때 붙일 이름 또는 None). 이 순서가 벡터의 순서다
FEATURES = {
    "points_ps": ("득점력", None),
    "attack_ps": ("공격 점유", None),
    "attack_rate": ("공격 성공률", None),
    "open_ps": ("오픈 공격", None),
    "quick_ps": ("속공", None),
    "back_ps": ("후위 공격", None),
    "ace_ps": ("서브 에이스", None),
    "block_ps": ("블로킹", None),
    "receive_ps": ("리시브 가담", None),
    "receive_eff": ("리시브 안정", None),
    "dig_ps": ("디그", None),
    "set_ps": ("세트(토스)", None),
    "error_ps": ("범실 많음", "범실 적음"),
}
NAMES = list(FEATURES)
POSITION_NAMES = {"OH": "아웃사이드 히터", "OP": "아포짓", "MB": "미들 블로커", "S": "세터", "L": "리베로"}
# 시즌 018까지 KOVO는 L(레프트), C(센터), R(라이트), Li(리베로)를 썼다. curate_kovo.py가 이미 모든 시즌을 현재
# 코드로 저장하므로 여기서 "L"은 언제나 리베로다. 현재 코드일 수 없는 값만 바꾼다(예전에 "L"을 한 번 더 바꾸는
# 바람에 2005~2019 리베로가 아웃사이드 히터로 분류됐던 문제를 2026-11-09에 고침).
OLD_ONLY = {"C": "MB", "R": "OP", "Li": "L"}


# ---------------------------------------------------------------- 순수 함수(spark_jobs/tests에서 테스트)
def normalize_position(season_code, position):
    """포지션 코드를 현재 체계로(남아 있는 옛 코드만 바꿈). 빈 값은 ''."""
    if not position:
        return ""
    return OLD_ONLY.get(position, position)


def choose_k(scores, tolerance=0.10):
    """{k: 실루엣} → 실루엣이 최고값의 (1 - tolerance) 배 이상인 k 중 가장 큰 k."""
    best = max(scores.values())
    return max(k for k, s in scores.items() if s >= best * (1 - tolerance))


# '얼마나 많이 하나'를 나타내는 특징들(보조형/균형형 판단에 씀)
VOLUME = ["points_ps", "attack_ps", "block_ps", "receive_ps", "dig_ps", "set_ps"]


def traits(deviation, n=3, threshold=0.35, allowed=None):
    """{특징: z 차이}에서 두드러지는 특징들을 [{feature, label, z}]로 고른다. 강점(차이가 큰 순)이 먼저, 범실은 맨 뒤.
    threshold보다 차이가 작은 특징은 뺀다. `allowed`: 강점으로 셀 수 있는 특징을 이것들로 제한."""
    found = []
    for f, d in deviation.items():
        high, low = FEATURES[f]
        label = high if d > 0 else low
        if label is None or abs(d) < threshold or (d > 0 and allowed is not None and f not in allowed):
            continue
        found.append({"feature": f, "label": label, "z": round(d, 2)})
    found.sort(key=lambda t: (t["feature"] == "error_ps", -abs(t["z"])))
    return found[:n]


def name_groups(groups):
    """한 성별의 그룹들([{cluster_no, position, size, centroid: {특징: z}}])에 이름(label)과 특징(traits)을 붙인다.

    같은 주 포지션의 그룹들("동료 그룹") 사이에서 아래 규칙을 차례로 적용한다.
      1. 동료 그룹 중 득점이 가장 많다(세트당 득점 z >= 1): 주포형
      2. 동료 그룹보다 더 하는 것(그중 평균 선수 이상으로 하는 것만)
      3. 그 포지션에 그룹이 하나뿐: 전체 선수 대비 강점
      4. 두드러지는 게 없음: 기록량이 대체로 평균 아래면 보조형, 아니면 균형형
    이름 옆에 보여 주는 `traits`는 전체 선수 대비 강점이라, 이름과 세부 설명이 서로 어긋나지 않는다.
    """
    by_pos = {}
    for g in groups:
        by_pos.setdefault(g["position"], []).append(g)
    for g in groups:
        c = g["centroid"]
        peers = by_pos[g["position"]]
        strengths = traits({f: v for f, v in c.items() if v > 0}, threshold=0.5)
        g["traits"] = [t for t in strengths if t["feature"] != "error_ps"]
        style = None
        if len(peers) > 1:
            if c["points_ps"] >= 1 and c["points_ps"] == max(p["centroid"]["points_ps"] for p in peers):
                style = "주포형"
            else:
                total = sum(p["size"] for p in peers)
                dev = {f: c[f] - sum(p["centroid"][f] * p["size"] for p in peers) / total for f in NAMES}
                own = traits(dev, allowed={f for f in NAMES if c[f] >= 0})
                if own and own[0]["z"] > 0:
                    style = own[0]["label"]
        elif g["traits"]:
            style = g["traits"][0]["label"]
        if style is None:
            style = "보조형" if sum(c[f] for f in VOLUME) / len(VOLUME) < -0.3 else "균형형"
        g["label"] = f"{POSITION_NAMES.get(g['position'], '기타')} · {style}"
    # 이름이 겹치면: 상대에게 없는 가장 강한 특징을 괄호로 덧붙여 구별한다
    seen = {}
    for g in groups:
        seen.setdefault(g["label"], []).append(g)
    for same in seen.values():
        if len(same) > 1:
            for g in same:
                extra = next((t["label"] for t in g["traits"] if t["label"] not in g["label"]), None)
                if extra:
                    g["label"] = f"{g['label']}({extra})"
    return groups


def nearest(z, keys, same_season_only, n=SIMILAR_N):
    """z: (행 × 특징) 배열, keys: [(선수 코드, 시즌 코드)].
    행마다 가장 가까운 '다른 선수'의 행 n개 [(인덱스, 거리)]와, 모든 선수 쌍 거리를 정렬한 배열을 돌려준다.
    same_season_only면 같은 시즌 행 중에서만 찾는다. 거리 = 유클리드 거리(모든 쌍을 한 번에 계산)."""
    import numpy as np

    d = np.sqrt(((z[:, None, :] - z[None, :, :]) ** 2).sum(-1))
    players = np.array([k[0] for k in keys])
    seasons = np.array([k[1] for k in keys])
    iu = np.triu_indices(len(keys), 1)
    pairs = np.sort(d[iu][players[iu[0]] != players[iu[1]]])
    out = []
    for i in range(len(keys)):
        mask = players != players[i]
        if same_season_only:
            mask &= seasons == seasons[i]
        idx = np.where(mask)[0]
        order = idx[np.argsort(d[i, idx])][:n]
        out.append([(int(j), float(d[i, j])) for j in order])
    return out, pairs


def similarity(distance, pairs):
    """유사도 0~100: (이 성별의) 모든 선수 쌍 중 이 쌍보다 더 멀리 떨어진 쌍의 비율. 100에 가까울수록 아주 비슷함."""
    import numpy as np

    return int(round(100 * (1 - np.searchsorted(pairs, distance) / max(1, len(pairs)))))


# ---------------------------------------------------------------- 특징 계산 (Spark)
def player_seasons(spark):
    """선수-시즌마다 특징 13개를 계산한다(정규리그, 자격 선수만)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR))
         .select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets", "away_sets"))
    p = read("player_match_stat").join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    sets = F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))
    p = p.withColumn("sets", sets)

    # 주 소속 팀 = 세트를 가장 많이 뛴 팀(시즌 중 이적할 수 있으므로), 포지션 = 가장 자주 기록된 값
    by_team = p.groupBy("season_code", "player_code", "team_code").agg(F.sum("sets").alias("team_sets_played"))
    w = Window.partitionBy("season_code", "player_code").orderBy(F.desc("team_sets_played"), "team_code")
    main_team = by_team.withColumn("_n", F.row_number().over(w)).where("_n = 1").select("season_code", "player_code", "team_code")
    sums = ["points", "attack_attempts", "attack_success", "serve_aces", "block_success", "receive_attempts",
            "receive_excellent", "receive_errors", "dig_success", "set_success", "errors"]
    agg = p.groupBy("season_code", "player_code", "gender").agg(
        F.sum("sets").alias("sets"), F.mode(F.when(F.col("position") != "", F.col("position"))).alias("position"),
        *[F.sum(c).alias(c) for c in sums])

    team_sets = m.select("season_code", F.explode(F.array("home_code", "away_code")).alias("team_code"),
                         (F.col("home_sets") + F.col("away_sets")).alias("s")) \
        .groupBy("season_code", "team_code").agg(F.sum("s").alias("team_sets"))
    agg = agg.join(main_team, ["season_code", "player_code"]).join(team_sets, ["season_code", "team_code"])
    agg = agg.where((F.col("sets") >= MIN_SETS) & (F.col("sets") >= F.col("team_sets") * MIN_SET_SHARE))

    att = read("player_attack_stat").join(m.select("season_code", "game_no"), ["season_code", "game_no"]) \
        .groupBy("season_code", "player_code").pivot("attack_type", ["OPEN", "QUICK", "QUICK_OPEN", "BACK"]) \
        .agg(F.sum("attempts")).na.fill(0)
    agg = agg.join(att, ["season_code", "player_code"], "left").na.fill(0, ["OPEN", "QUICK", "QUICK_OPEN", "BACK"])

    per_set = lambda c: F.col(c) / F.col("sets")  # noqa: E731
    league = agg.groupBy("gender").agg(
        (F.sum("attack_success") / F.sum("attack_attempts")).alias("mean_attack"),
        ((F.sum("receive_excellent") - F.sum("receive_errors")) / F.sum("receive_attempts")).alias("mean_receive"))
    agg = agg.join(league, "gender")
    # 당긴 비율: (성공 + 평균 × 20) / (시도 + 20). 세터의 공격 5번이 진짜 공격 성공률처럼 쓰이지 않게
    shrunk = lambda made, attempts, mean: (made + F.col(mean) * PRIOR_ATTEMPTS) / (F.col(attempts) + PRIOR_ATTEMPTS)  # noqa: E731
    return agg.select(
        "season_code", "player_code", "team_code", "gender", "position", "sets",
        per_set("points").alias("points_ps"), per_set("attack_attempts").alias("attack_ps"),
        shrunk(F.col("attack_success"), "attack_attempts", "mean_attack").alias("attack_rate"),
        # 퀵오픈은 날개 공격수의 빠른 공격이라 오픈 공격과 함께 센다. 속공은 미들 블로커의 공격
        ((F.col("OPEN") + F.col("QUICK_OPEN")) / F.col("sets")).alias("open_ps"), per_set("QUICK").alias("quick_ps"),
        per_set("BACK").alias("back_ps"), per_set("serve_aces").alias("ace_ps"), per_set("block_success").alias("block_ps"),
        per_set("receive_attempts").alias("receive_ps"),
        shrunk(F.col("receive_excellent") - F.col("receive_errors"), "receive_attempts", "mean_receive").alias("receive_eff"),
        per_set("dig_success").alias("dig_ps"), per_set("set_success").alias("set_ps"), per_set("errors").alias("error_ps"))


# ---------------------------------------------------------------- 성별 하나
def fit_gender(spark, rows, gender, mlflow):
    """한 성별의 군집화 전체: 표준화 → k별 K-means(MLflow 기록) → k 선택 → 모든 선수 배정 → 그룹 이름 → 비슷한 선수.
    (player_type 행들, player_style 행들)을 돌려준다."""
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.evaluation import ClusteringEvaluator
    from pyspark.ml.feature import StandardScaler, VectorAssembler
    from pyspark.sql import functions as F

    df = rows.where(F.col("gender") == gender)
    fit_rows = df.where(F.col("sets") >= FIT_SETS).cache()

    assembler = VectorAssembler(inputCols=NAMES, outputCol="raw")
    scaler = StandardScaler(inputCol="raw", outputCol="features", withMean=True, withStd=True).fit(assembler.transform(fit_rows))
    fit_data = scaler.transform(assembler.transform(fit_rows)).cache()
    evaluator = ClusteringEvaluator(featuresCol="features", predictionCol="cluster")
    models, scores = {}, {}
    with mlflow.start_run(run_name=f"player-types-{gender}") as run:
        mlflow.log_params({"gender": gender, "features": ",".join(NAMES), "fit_rows": fit_data.count(),
                           "min_sets": FIT_SETS, "k_range": f"{K_RANGE.start}-{K_RANGE.stop - 1}"})
        for k in K_RANGE:
            model = KMeans(k=k, seed=SEED, featuresCol="features", predictionCol="cluster", maxIter=50).fit(fit_data)
            scores[k] = evaluator.evaluate(model.transform(fit_data))
            models[k] = model
            mlflow.log_metric("silhouette", scores[k], step=k)
        k = choose_k(scores)
        mlflow.log_params({"chosen_k": k})
        mlflow.log_metric("chosen_silhouette", scores[k])
        run_id = run.info.run_id
    print(f"{gender}: silhouette by k {json.dumps({k: round(v, 3) for k, v in scores.items()})} -> k={k}")

    model = models[k]
    assigned = model.transform(scaler.transform(assembler.transform(df))).cache()
    mean, std = scaler.mean.toArray(), scaler.std.toArray()
    rows_out = assigned.select("season_code", "player_code", "team_code", "position", "sets", "cluster",
                               *NAMES).collect()
    import numpy as np

    # 모든 선수(잠정 포함)의 표준화 값. 학습 데이터의 평균·표준편차로 맞춘다
    z = np.array([[(r[f] - mean[i]) / (std[i] or 1) for i, f in enumerate(NAMES)] for r in rows_out])
    positions = [normalize_position(r.season_code, r.position) for r in rows_out]

    groups = []  # 그룹마다 크기(30세트 이상 선수 수), 중심(특징별 z), 주 포지션(가장 많은 포지션)
    for c, centre in enumerate(model.clusterCenters()):
        members = [i for i, r in enumerate(rows_out) if r.cluster == c and r.sets >= FIT_SETS]
        pos_count = {}
        for i in members:
            pos_count[positions[i]] = pos_count.get(positions[i], 0) + 1
        groups.append({"cluster_no": c, "size": len(members), "centroid": {f: round(float(v), 3) for f, v in zip(NAMES, centre)},
                       "position": max((p for p in pos_count if p), key=pos_count.get, default="")})
    name_groups(groups)
    for g in groups:
        print(f"  {gender} {g['cluster_no']}: {g['label']} ({g['size']}) {[t['label'] for t in g['traits']]}")

    keys = [(r.player_code, r.season_code) for r in rows_out]
    near_season, pairs = nearest(z, keys, True)
    near_all, _ = nearest(z, keys, False)

    def ref(j, dist):
        """비슷한 선수 하나를 화면에 보여 줄 모양으로."""
        r = rows_out[j]
        return {"player": r.player_code, "season": r.season_code, "team": r.team_code,
                "type": groups[r.cluster]["label"], "similarity": similarity(dist, pairs)}

    styles = []
    for i, r in enumerate(rows_out):
        styles.append((r.season_code, r.player_code, r.team_code, gender, int(r.cluster), int(r.sets), r.sets < FIT_SETS,
                       json.dumps({f: round(float(r[f]), 4) for f in NAMES}),
                       json.dumps({f: round(float(v), 3) for f, v in zip(NAMES, z[i])}),
                       json.dumps([ref(j, d) for j, d in near_season[i]], ensure_ascii=False),
                       json.dumps([ref(j, d) for j, d in near_all[i]], ensure_ascii=False)))
    types = [(gender, g["cluster_no"], g["label"], g["position"], json.dumps(g["traits"], ensure_ascii=False),
              json.dumps(g["centroid"]), g["size"], k, float(scores[k]), run_id) for g in groups]
    return types, styles


def main():
    """남녀 각각 군집화하고 결과를 PostgreSQL에 병합한다."""
    import mlflow
    from pyspark.sql import SparkSession

    from pg_merge import load_and_merge

    spark = (SparkSession.builder.appName("player-types")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    mlflow.set_experiment(EXPERIMENT)
    rows = player_seasons(spark).cache()
    types, styles = [], []
    for gender in ("M", "F"):
        t, s = fit_gender(spark, rows, gender, mlflow)
        types += t
        styles += s
    tables = {
        "player_type": spark.createDataFrame(types, "gender string, cluster_no int, label string, position string, "
                                                    "traits string, centroid string, size int, k int, silhouette double, "
                                                    "mlflow_run_id string"),
        "player_style": spark.createDataFrame(styles, "season_code string, player_code string, team_code string, "
                                                      "gender string, cluster_no int, sets int, provisional boolean, "
                                                      "features string, z string, similar_season string, similar_all string"),
    }
    for row in load_and_merge(spark, tables, MERGE_SQL, "merge_player_types"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
