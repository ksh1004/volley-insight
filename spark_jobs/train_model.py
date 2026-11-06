"""Train the match outcome model (ANL-03, ANL-09): Spark MLlib + Hyperopt, tracked in MLflow.

  input   HDFS /features/kovo/match/ (build_features.py), finished matches only
  output  MLflow experiment `match-winner` (parent run + one nested run per Hyperopt trial),
          registered model `match-winner` with alias `champion` (serving; requirements 2.6)

Target: the home team's set score as 6 classes (3:0, 3:1, 3:2, 2:3, 1:3, 0:3). The home win probability is
P(3:0)+P(3:1)+P(3:2); the predicted set score is the most likely class.

Time split, never random (a random split would let the model learn from later games):
  test        latest season with >= 100 finished games
  validation  the two seasons before it  -> Hyperopt picks model type and settings on these
  train       everything older
The chosen settings are refit on train+validation and scored on test (reported metrics), then refit on all
seasons for the registered model. Baseline: the pre-match Elo probability on the same test season.

Champion: the current champion's settings (model type + hyperparameters, from its run) are retrained on the
same train+validation data and scored on the same test season as the newly searched settings. Whichever is
better (ties go to the new settings) is fit on every season and registered as the new `champion`, so the
serving model always has the latest data while a better configuration is only adopted when it really wins.
(Numbers recorded by older runs are not compared: features and seasons change between runs.)

Walk-forward backtest (ANL-10): with the winning settings, each of the last BACKTEST_SEASONS seasons is predicted by a
model trained only on the seasons before it. Per-season metrics (and Elo's on the same matches) go to MLflow, and the
predictions to PostgreSQL prediction_backtest for the site's verification page.

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh train_model.py [--evals 24]
"""
import argparse
import json
import math
import os

import sys
from pathlib import Path

import mlflow
import mlflow.spark
from hyperopt import STATUS_OK, Trials, fmin, hp, tpe
from mlflow.tracking import MlflowClient
from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression, RandomForestClassifier
from pyspark.ml.feature import StandardScaler, VectorAssembler
from pyspark.ml.functions import vector_to_array
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

FEATURES = os.environ.get("FEATURES_PATH", "hdfs://namenode:9000/features/kovo")
EXPERIMENT = "match-winner"
MODEL_NAME = "match-winner"
FEATURE_COLS = ["elo_diff", "elo_prob", "form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive",
                "block", "ace", "error", "rest", "h2h_rate", "h2h_n", "postseason", "men"]
BACKTEST_SEASONS = 4
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_backtest.sql"
SET_SCORES = ["3:0", "3:1", "3:2", "2:3", "1:3", "0:3"]
EPS = 1e-6


def pipeline(params):
    assembler = VectorAssembler(inputCols=FEATURE_COLS, outputCol="raw", handleInvalid="keep")
    if params["model"] == "lr":
        scaler = StandardScaler(inputCol="raw", outputCol="features", withMean=True, withStd=True)
        clf = LogisticRegression(family="multinomial", labelCol="set_score", featuresCol="features", maxIter=200,
                                 regParam=params["reg"], elasticNetParam=params["l1"])
        return Pipeline(stages=[assembler, scaler, clf])
    clf = RandomForestClassifier(labelCol="set_score", featuresCol="raw", seed=7, numTrees=int(params["trees"]),
                                 maxDepth=int(params["depth"]), minInstancesPerNode=int(params["min_leaf"]))
    return Pipeline(stages=[assembler, clf])


def scores(pred_df):
    """Winner accuracy, winner log loss / Brier, set-score accuracy and log loss, from a scored DataFrame."""
    rows = pred_df.select("set_score", vector_to_array("probability").alias("p")).collect()
    n = max(1, len(rows))
    acc = ll = brier = set_acc = set_ll = 0.0
    for r in rows:
        p = r.p
        y = int(r.set_score)
        p_home = min(max(sum(p[:3]), EPS), 1 - EPS)
        won = y < 3
        acc += (p_home >= 0.5) == won
        ll -= math.log(p_home if won else 1 - p_home)
        brier += (p_home - won) ** 2
        set_acc += max(range(6), key=lambda i: p[i]) == y
        set_ll -= math.log(max(p[y], EPS))
    return {"accuracy": acc / n, "log_loss": ll / n, "brier": brier / n, "set_accuracy": set_acc / n,
            "set_log_loss": set_ll / n, "n": len(rows)}


def elo_scores(df):
    rows = df.select("elo_prob", "home_win").collect()
    n = max(1, len(rows))
    acc = sum((r.elo_prob >= 0.5) == (r.home_win == 1) for r in rows) / n
    ll = -sum(math.log(min(max(r.elo_prob if r.home_win == 1 else 1 - r.elo_prob, EPS), 1 - EPS)) for r in rows) / n
    brier = sum((r.elo_prob - r.home_win) ** 2 for r in rows) / n
    return {"accuracy": acc, "log_loss": ll, "brier": brier, "n": len(rows)}


def explain(model, params):
    """Feature importance (forest) or coefficient size per feature summed over classes (logistic)."""
    stage = model.stages[-1]
    if params["model"] == "rf":
        imp = stage.featureImportances.toArray()
    else:
        coef = stage.coefficientMatrix.toArray()  # classes x features (standardized inputs)
        imp = [sum(abs(coef[c][j]) for c in range(len(coef))) for j in range(len(FEATURE_COLS))]
    total = sum(imp) or 1
    return dict(sorted(((f, round(float(v) / total, 4)) for f, v in zip(FEATURE_COLS, imp)), key=lambda x: -x[1]))


def split(df):
    counts = {r.season_code: r["count"] for r in df.groupBy("season_code").count().collect()}
    seasons = sorted(counts)
    test = max(s for s in seasons if counts[s] >= 100)
    before = [s for s in seasons if s < test]
    val, train = before[-2:], before[:-2]
    return train, val, test


def backtest(data, settings, seasons):
    """Walk-forward: each season predicted by a model trained only on the seasons before it.
    Returns (rows for prediction_backtest, {season: (model scores, elo scores)})."""
    rows, per_season = [], {}
    for season in seasons:
        past = data.where(F.col("season_code") < season)
        target = data.where(F.col("season_code") == season)
        first, last = past.agg(F.min("season_code"), F.max("season_code")).first()
        scored = pipeline(settings).fit(past).transform(target).cache()
        per_season[season] = (scores(scored), elo_scores(target))
        for r in scored.select("season_code", "game_no", "elo_prob", "home_win",
                               vector_to_array("probability").alias("p")).collect():
            home, away = SET_SCORES[max(range(6), key=lambda i: r.p[i])].split(":")
            rows.append((r.season_code, int(r.game_no), round(float(sum(r.p[:3])), 4), round(float(r.elo_prob), 4),
                         int(home), int(away), bool(r.home_win == 1), f"{first}-{last}"))
        scored.unpersist()
    return rows, per_season


def champion_settings(client):
    """(version, settings) of the current champion, read from its training run; None if there is none."""
    try:
        champ = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except Exception:
        return None
    params = client.get_run(champ.run_id).data.params
    prefix = "final_" if any(k.startswith("final_") for k in params) else "best_"  # older runs logged best_ only
    settings = {k.removeprefix(prefix): v for k, v in params.items() if k.startswith(prefix)}
    for k in ("reg", "l1", "trees", "depth", "min_leaf"):
        if k in settings:
            settings[k] = float(settings[k])
    return champ.version, settings


def save_backtest(spark, rows, version, run_id, metrics, elo, train_seasons, test_season):
    """prediction_backtest rows for this version (and its model_version row if predictions have not made it yet)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pg_merge import load_and_merge

    mv = spark.createDataFrame([(MODEL_NAME, int(version), run_id, json.dumps(
        {**{k: v for k, v in metrics.items()}, **{f"elo_{k}": v for k, v in elo.items()}}), train_seasons, test_season)],
        "name string, version int, run_id string, metrics string, train_seasons string, test_season string")
    bt = spark.createDataFrame(rows, "season_code string, game_no int, home_win_prob double, elo_home_win_prob double, "
                                     "pred_home_sets int, pred_away_sets int, home_won boolean, train_seasons string")
    for row in load_and_merge(spark, {"model_version": mv, "prediction_backtest": bt}, MERGE_SQL, "merge_backtest"):
        print("merge:", *row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals", type=int, default=24, help="Hyperopt trials")
    args = ap.parse_args()
    spark = SparkSession.builder.appName("train-match-winner").getOrCreate()
    data = spark.read.parquet(f"{FEATURES}/match").where(F.col("set_score").isNotNull()).cache()
    train_s, val_s, test_s = split(data)
    train = data.where(F.col("season_code").isin(train_s)).cache()
    val = data.where(F.col("season_code").isin(val_s)).cache()
    trainval = data.where(F.col("season_code").isin(train_s + val_s)).cache()
    test = data.where(F.col("season_code") == test_s).cache()
    cutoff = data.agg(F.max("game_date")).first()[0]
    print(f"train {train_s[0]}-{train_s[-1]} ({train.count()}), validation {val_s} ({val.count()}), test {test_s} ({test.count()})")

    mlflow.set_experiment(EXPERIMENT)
    space = hp.choice("model", [
        {"model": "lr", "reg": hp.loguniform("reg", math.log(1e-4), math.log(1.0)), "l1": hp.uniform("l1", 0.0, 1.0)},
        {"model": "rf", "trees": hp.choice("trees", [50, 100, 200]), "depth": hp.quniform("depth", 3, 8, 1),
         "min_leaf": hp.choice("min_leaf", [5, 10, 20, 40])},
    ])

    with mlflow.start_run(run_name=f"train-{cutoff}") as parent:
        mlflow.set_tags({"data_cutoff": str(cutoff), "test_season": test_s})
        mlflow.log_params({"features": ",".join(FEATURE_COLS), "train_seasons": f"{train_s[0]}-{train_s[-1]}",
                           "validation_seasons": ",".join(val_s), "test_season": test_s, "hyperopt_evals": args.evals})

        def objective(params):
            with mlflow.start_run(nested=True):
                mlflow.log_params(params)
                m = scores(pipeline(params).fit(train).transform(val))
                mlflow.log_metrics({f"val_{k}": v for k, v in m.items()})
            return {"loss": m["log_loss"], "status": STATUS_OK, "params": params}

        trials = Trials()
        fmin(objective, space, algo=tpe.suggest, max_evals=args.evals, trials=trials, show_progressbar=False)
        best = min(trials.results, key=lambda r: r["loss"])["params"]
        print("best settings:", best)
        mlflow.log_params({f"best_{k}": v for k, v in best.items()})

        tested = pipeline(best).fit(trainval)
        test_m = scores(tested.transform(test))
        base = elo_scores(test)
        mlflow.log_metrics({**{f"test_{k}": v for k, v in test_m.items()}, **{f"elo_test_{k}": v for k, v in base.items()}})
        print("test:", json.dumps({k: round(v, 4) for k, v in test_m.items()}))
        champion = champion_settings(MlflowClient())
        champion_ll = None
        if champion:
            champion_m = scores(pipeline(champion[1]).fit(trainval).transform(test))
            champion_ll = champion_m["log_loss"]
            mlflow.log_metrics({f"champion_test_{k}": v for k, v in champion_m.items()})
            print(f"champion v{champion[0]} settings on the same data:", json.dumps({k: round(v, 4) for k, v in champion_m.items()}))
        print("elo :", json.dumps({k: round(v, 4) for k, v in base.items()}))

        # Keep whichever settings did better on the same data, then fit them on every season.
        if champion and champion_ll < test_m["log_loss"]:
            winner, winner_m = champion[1], champion_m
            decision = (f"champion v{champion[0]} settings kept ({champion_ll:.4f} < new {test_m['log_loss']:.4f}), "
                        "refit on the latest data")
        else:
            winner, winner_m = best, test_m
            decision = ("first model" if not champion else
                        f"new settings ({test_m['log_loss']:.4f} <= champion v{champion[0]} settings {champion_ll:.4f})")
        mlflow.log_params({f"final_{k}": v for k, v in winner.items()})
        mlflow.log_metrics({f"final_test_{k}": v for k, v in winner_m.items()})

        final = pipeline(winner).fit(data)
        importance = explain(final, winner)
        mlflow.log_dict(importance, "feature_importance.json")
        mlflow.log_dict({"set_scores": SET_SCORES, "features": FEATURE_COLS}, "model_schema.json")
        print("feature importance:", importance)
        info = mlflow.spark.log_model(final, artifact_path="model", registered_model_name=MODEL_NAME)

        client = MlflowClient()
        version = info.registered_model_version
        for k, v in {"test_season": test_s, "test_log_loss": f"{winner_m['log_loss']:.6f}",
                     "test_accuracy": f"{winner_m['accuracy']:.4f}", "elo_test_log_loss": f"{base['log_loss']:.6f}",
                     "train_seasons": f"{train_s[0]}-{test_s}", "data_cutoff": str(cutoff), "run_id": parent.info.run_id}.items():
            client.set_model_version_tag(MODEL_NAME, version, k, v)
        client.set_registered_model_alias(MODEL_NAME, "champion", version)
        try:
            client.delete_registered_model_alias(MODEL_NAME, "candidate")
        except Exception:
            pass
        mlflow.set_tag("registry_decision", decision)
        print(f"registered {MODEL_NAME} v{version} as champion: {decision}")

        seasons = sorted(r.season_code for r in data.select("season_code").distinct().collect())
        bt_seasons = [x for x in seasons if x <= test_s][-BACKTEST_SEASONS:]
        bt_rows, per_season = backtest(data, winner, bt_seasons)
        for season, (m, e) in per_season.items():
            mlflow.log_metrics({f"backtest_{season}_accuracy": m["accuracy"], f"backtest_{season}_log_loss": m["log_loss"],
                                f"backtest_{season}_elo_accuracy": e["accuracy"], f"backtest_{season}_elo_log_loss": e["log_loss"]})
            print(f"backtest {season}: model acc {m['accuracy']:.3f} ll {m['log_loss']:.4f} | "
                  f"elo acc {e['accuracy']:.3f} ll {e['log_loss']:.4f} (n={m['n']})")
        save_backtest(spark, bt_rows, version, parent.info.run_id, winner_m, base, f"{train_s[0]}-{test_s}", test_s)
    spark.stop()


if __name__ == "__main__":
    main()
