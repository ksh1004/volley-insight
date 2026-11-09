"""Player types and similar players (ANL-06, WEB-07): K-means on per-set records with Spark MLlib.

  HDFS /curated/kovo/{match,player_match_stat,player_attack_stat}   regular season, all seasons
    -> one row per player-season: 13 style features (per-set volumes, attack-type volumes, two rates)
    -> per gender: standardise, K-means for k = 4..9, keep the largest k whose silhouette is within 10% of the best
       (more, finer types as long as they stay about as well separated); every k is logged to MLflow `player-types`
    -> each group named after its main position and what sets it apart from the other groups of that position
    -> nearest players by distance between standardised features: same season, and other players' seasons (all time)
    -> PostgreSQL player_type, player_style (full refresh, sql/merge_player_types.sql)

Who is in: players with at least a third of their team's sets (the ranking rule) and 10 sets; groups are fitted on
players with 30+ sets so a handful of sets cannot shape them (fewer sets: assigned, marked provisional).
Positions come from the curated data, which already uses the current codes for every season.

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh player_types.py   (also in kovo_daily)
"""
import json
import os
from pathlib import Path

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_player_types.sql"
EXPERIMENT = "player-types"
FINISHED = ["FINISHED", "CONFIRMED"]
REGULAR = "201"
MIN_SET_SHARE, MIN_SETS, FIT_SETS = 1 / 3, 10, 30
PRIOR_ATTEMPTS = 20  # rates are shrunk toward the average as if 20 average attempts were added (few attempts -> average)
K_RANGE = range(4, 10)
SIMILAR_N = 5
SEED = 7

# feature -> (label when high, label when low or None); the order is the vector order
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
# Up to season 018 KOVO used L (left), C (centre), R (right), Li (libero). curate_kovo.py already stores the current
# codes for every season, so "L" here always means libero. Only codes that cannot be current ones are mapped (a
# second mapping of "L" turned the 2005-2019 liberos into outside hitters until 2026-11-09).
OLD_ONLY = {"C": "MB", "R": "OP", "Li": "L"}


# ---------------------------------------------------------------- pure helpers (tested in spark_jobs/tests)
def normalize_position(season_code, position):
    if not position:
        return ""
    return OLD_ONLY.get(position, position)


def choose_k(scores, tolerance=0.10):
    """{k: silhouette} -> the largest k whose silhouette is at least (1 - tolerance) x the best."""
    best = max(scores.values())
    return max(k for k, s in scores.items() if s >= best * (1 - tolerance))


VOLUME = ["points_ps", "attack_ps", "block_ps", "receive_ps", "dig_ps", "set_ps"]


def traits(deviation, n=3, threshold=0.35, allowed=None):
    """Features that stand out in a {feature: z difference}: [{feature, label, z}], strengths first (largest first),
    the error rate last. `allowed`: only these features may count as strengths."""
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
    """groups: [{cluster_no, position, size, centroid: {feature: z}}] of one gender. Adds label and traits.

    Rules, in order, for a group among the groups of its main position ("peers"):
      1. it scores the most of its peers (points per set z >= 1): 주포형 (main scorer)
      2. what it does more than its peers, among things it does at least as much as an average player
      3. a position with one group: its strengths against all players
      4. nothing stands out: 보조형 (role player) when its volumes are mostly below average, else 균형형
    `traits` (shown next to the name) are its strengths against all players, so the name and the details agree.
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
    # same name twice: add the strongest trait the other one does not share
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
    """z: (rows x features) array; keys: [(player_code, season_code)].
    Returns for each row the n nearest other players' rows [(index, distance)] and the sorted pair distances."""
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
    """0-100: share of all player pairs (of this gender) that are farther apart than this one."""
    import numpy as np

    return int(round(100 * (1 - np.searchsorted(pairs, distance) / max(1, len(pairs)))))


# ---------------------------------------------------------------- features (Spark)
def player_seasons(spark):
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR))
         .select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets", "away_sets"))
    p = read("player_match_stat").join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    sets = F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))
    p = p.withColumn("sets", sets)

    # main team = the one with most sets (a player can move mid-season); position = most common
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
    # shrunk rates: (made + mean x 20) / (attempts + 20), so a setter's 5 attacks do not count as a real rate
    shrunk = lambda made, attempts, mean: (made + F.col(mean) * PRIOR_ATTEMPTS) / (F.col(attempts) + PRIOR_ATTEMPTS)  # noqa: E731
    return agg.select(
        "season_code", "player_code", "team_code", "gender", "position", "sets",
        per_set("points").alias("points_ps"), per_set("attack_attempts").alias("attack_ps"),
        shrunk(F.col("attack_success"), "attack_attempts", "mean_attack").alias("attack_rate"),
        # 퀵오픈 is a wing hitter's fast attack: counted with open attacks; 속공 (quick) is the middle's
        ((F.col("OPEN") + F.col("QUICK_OPEN")) / F.col("sets")).alias("open_ps"), per_set("QUICK").alias("quick_ps"),
        per_set("BACK").alias("back_ps"), per_set("serve_aces").alias("ace_ps"), per_set("block_success").alias("block_ps"),
        per_set("receive_attempts").alias("receive_ps"),
        shrunk(F.col("receive_excellent") - F.col("receive_errors"), "receive_attempts", "mean_receive").alias("receive_eff"),
        per_set("dig_success").alias("dig_ps"), per_set("set_success").alias("set_ps"), per_set("errors").alias("error_ps"))


# ---------------------------------------------------------------- one gender
def fit_gender(spark, rows, gender, mlflow):
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

    z = np.array([[(r[f] - mean[i]) / (std[i] or 1) for i, f in enumerate(NAMES)] for r in rows_out])
    positions = [normalize_position(r.season_code, r.position) for r in rows_out]

    groups = []
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
