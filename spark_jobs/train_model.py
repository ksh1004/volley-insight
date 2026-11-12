"""경기 결과 예측 모델을 학습한다(ANL-03, ANL-09): Spark MLlib + Hyperopt, 기록은 MLflow.

  입력  HDFS /features/kovo/match/ (build_features.py), 끝난 경기만
  출력  MLflow 실험 `match-winner` (부모 실행 + Hyperopt 시도마다 하위 실행 하나),
        등록 모델 `match-winner`의 별칭 `champion` (서비스에 쓰는 모델, 요구사항 2.6)

맞힐 대상: 홈 팀의 세트 스코어 6가지(3:0, 3:1, 3:2, 2:3, 1:3, 0:3) 중 하나(다중 분류).
홈 승리 확률 = P(3:0)+P(3:1)+P(3:2), 예상 세트 스코어 = 가장 확률이 높은 칸.

시간 순서로 나눈다. 절대 무작위로 나누지 않는다(무작위면 미래 경기로 과거를 배우는 셈이라 성적이 부풀려진다):
  test(시험)        끝난 경기가 100개 이상인 가장 최근 시즌
  validation(검증)  그 앞의 두 시즌 → Hyperopt가 이 시즌들로 모델 종류와 설정을 고른다
  train(학습)       그보다 오래된 모든 시즌
고른 설정은 학습+검증 시즌으로 다시 학습해 시험 시즌으로 채점하고(보고되는 성적), 등록할 모델은 모든 시즌으로
다시 학습한다. 비교 기준(baseline): 같은 시험 시즌에서 경기 전 Elo 확률만으로 예측한 성적.

챔피언 규칙: 지금 챔피언의 설정(모델 종류 + 하이퍼파라미터, 그 실행 기록에서 읽음)도 같은 학습+검증 데이터로 다시
학습해, 새로 찾은 설정과 같은 시험 시즌에서 겨룬다. 더 나은 쪽(같으면 새 설정)을 모든 시즌으로 학습해 새
`champion`으로 등록한다. 그래서 서비스 모델은 항상 최신 데이터를 배우고, 설정은 정말로 이길 때만 바뀐다.
(예전 실행이 남긴 숫자와는 비교하지 않는다: 실행마다 특징과 시즌이 달라져서 공정하지 않다.)

워크포워드 백테스트(ANL-10): 이긴 설정으로, 최근 BACKTEST_SEASONS개 시즌을 각각 '그 이전 시즌들로만 학습한 모델'로
예측한다(실제로 그 시즌 개막 전에 예측했다면 얼마나 맞혔을지). 시즌별 성적(같은 경기의 Elo 성적 포함)은 MLflow에,
예측들은 사이트의 '예측 검증' 화면용으로 PostgreSQL prediction_backtest에 남긴다.

용어: 로그 손실(log loss) = 확률 예측이 얼마나 정확한지(낮을수록 좋음. 틀린 쪽에 자신 있게 걸수록 크게 벌점),
      Brier = 확률과 실제(0/1)의 차이 제곱 평균(낮을수록 좋음).

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh train_model.py [--evals 24] (평소에는 ml_weekly_retrain)
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
# 모델에 넣는 특징(build_features.py가 만든 컬럼 중에서). 바꾸면 다음 학습부터 새 모델에 반영된다
# 모델 입력. 팀 Elo(elo_*) 대신 선수 Elo 팀 전력(pelo_*)을 쓴다: 2026-11-12 챔피언 설정으로 016~022 walk-forward 비교에서
# 팀 Elo 0.6277 → 선수 Elo 0.6251(둘 다 넣으면 0.6259), 7시즌 모두 개선. elo_prob는 비교 기준(elo_scores)으로만 남는다.
FEATURE_COLS = ["pelo_diff", "pelo_prob", "form_win", "form_setdiff", "form_pointdiff", "pointdiff", "attack", "receive",
                "block", "ace", "error", "rest", "h2h_rate", "h2h_n", "postseason", "men", "squad", "squad_known"]
BACKTEST_SEASONS = 4  # 백테스트할 최근 시즌 수
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_backtest.sql"
SET_SCORES = ["3:0", "3:1", "3:2", "2:3", "1:3", "0:3"]
EPS = 1e-6  # log(0)을 막기 위한 아주 작은 값


def pipeline(params):
    """설정(params)으로 학습 파이프라인을 만든다.
    lr: 특징 묶기 → 표준화 → 다항 로지스틱 회귀(reg 규제 강도, l1 = L1/L2 섞는 비율)
    rf: 특징 묶기 → 랜덤 포레스트(trees 나무 수, depth 최대 깊이, min_leaf 잎의 최소 경기 수)
    handleInvalid="keep": 값이 비어 있는 행도 버리지 않는다(예정 경기를 빠짐없이 예측하기 위해)."""
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
    """예측 결과 DataFrame으로 성적을 낸다: 승패 적중률, 승패 로그 손실·Brier, 세트 스코어 적중률·로그 손실."""
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
    """같은 경기들에서 경기 전 Elo 확률만으로 예측했을 때의 성적(비교 기준)."""
    rows = df.select("elo_prob", "home_win").collect()
    n = max(1, len(rows))
    acc = sum((r.elo_prob >= 0.5) == (r.home_win == 1) for r in rows) / n
    ll = -sum(math.log(min(max(r.elo_prob if r.home_win == 1 else 1 - r.elo_prob, EPS), 1 - EPS)) for r in rows) / n
    brier = sum((r.elo_prob - r.home_win) ** 2 for r in rows) / n
    return {"accuracy": acc, "log_loss": ll, "brier": brier, "n": len(rows)}


def explain(model, params):
    """특징별 중요도: 랜덤 포레스트면 feature importance, 로지스틱이면 클래스별 계수 크기의 합(표준화된 입력 기준).
    합이 1이 되게 나눠 큰 순서로 돌려준다(MLflow에 남겨 어떤 특징이 중요한지 확인)."""
    stage = model.stages[-1]
    if params["model"] == "rf":
        imp = stage.featureImportances.toArray()
    else:
        coef = stage.coefficientMatrix.toArray()  # classes x features (standardized inputs)
        imp = [sum(abs(coef[c][j]) for c in range(len(coef))) for j in range(len(FEATURE_COLS))]
    total = sum(imp) or 1
    return dict(sorted(((f, round(float(v) / total, 4)) for f, v in zip(FEATURE_COLS, imp)), key=lambda x: -x[1]))


def split(df):
    """(학습 시즌들, 검증 시즌들, 시험 시즌)을 정한다. 시험 = 경기 100개 이상인 가장 최근 시즌, 검증 = 그 앞 두 시즌."""
    counts = {r.season_code: r["count"] for r in df.groupBy("season_code").count().collect()}
    seasons = sorted(counts)
    test = max(s for s in seasons if counts[s] >= 100)
    before = [s for s in seasons if s < test]
    val, train = before[-2:], before[:-2]
    return train, val, test


def backtest(data, settings, seasons):
    """워크포워드: 각 시즌을 '그 이전 시즌들로만 학습한 모델'로 예측한다.
    (prediction_backtest에 넣을 행들, {시즌: (모델 성적, Elo 성적)})을 돌려준다."""
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
    """지금 챔피언의 (버전, 설정). 설정은 그 모델을 만든 학습 실행의 파라미터에서 읽는다. 챔피언이 없으면 None."""
    try:
        champ = client.get_model_version_by_alias(MODEL_NAME, "champion")
    except Exception:
        return None
    params = client.get_run(champ.run_id).data.params
    prefix = "final_" if any(k.startswith("final_") for k in params) else "best_"  # 예전 실행은 best_만 남겼다
    settings = {k.removeprefix(prefix): v for k, v in params.items() if k.startswith(prefix)}
    for k in ("reg", "l1", "trees", "depth", "min_leaf"):
        if k in settings:
            settings[k] = float(settings[k])
    return champ.version, settings


def save_backtest(spark, rows, version, run_id, metrics, elo, train_seasons, test_season):
    """이 모델 버전의 백테스트 행들을 저장한다(아직 예측을 안 만들어 model_version 행이 없으면 그것도 함께)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pg_merge import load_and_merge

    # 성적 이름은 predict_matches.py와 같게(test_*, elo_test_*): 사이트(analytics/queries.py)가 이 이름으로 읽는다
    mv = spark.createDataFrame([(MODEL_NAME, int(version), run_id, json.dumps(
        {**{f"test_{k}": v for k, v in metrics.items()}, **{f"elo_test_{k}": v for k, v in elo.items()}}),
        train_seasons, test_season)],
        "name string, version int, run_id string, metrics string, train_seasons string, test_season string")
    bt = spark.createDataFrame(rows, "season_code string, game_no int, home_win_prob double, elo_home_win_prob double, "
                                     "pred_home_sets int, pred_away_sets int, home_won boolean, train_seasons string")
    for row in load_and_merge(spark, {"model_version": mv, "prediction_backtest": bt}, MERGE_SQL, "merge_backtest"):
        print("merge:", *row)


def main():
    """전체 흐름: 데이터 나누기 → Hyperopt 탐색 → 시험 → 챔피언과 비교 → 승자를 전체 데이터로 학습·등록 → 백테스트."""
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
    # 탐색 공간: 로지스틱 회귀(규제 강도는 로그 균등, L1 비율 0~1) 또는 랜덤 포레스트(나무 수·깊이·잎 크기)
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
            """Hyperopt가 최소화할 값: 이 설정으로 학습 시즌에 학습해 검증 시즌에서 낸 로그 손실."""
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

        # 같은 데이터에서 더 잘한 설정을 남기고, 그 설정을 모든 시즌으로 학습한다.
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

        final = pipeline(winner).fit(data)  # 서비스용: 모든 끝난 경기로 학습
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
        client.set_registered_model_alias(MODEL_NAME, "champion", version)  # 이제 이 버전이 예측에 쓰인다
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
