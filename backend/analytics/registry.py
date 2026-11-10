"""ADM-05: MLflow 모델 저장소의 버전 목록을 읽고, 서비스에 쓰는 모델(champion)을 바꾼다.

MLflow REST API(웹 이미지에는 mlflow 패키지가 없음)와, 예측을 다시 돌리기 위한 Airflow REST API를 쓴다.
두 서비스는 compose의 `batch` 프로필에 있다. 꺼져 있으면 관리자 화면이 오류로 죽지 않고 그렇다고 알려 준다.
"""
import os
import urllib.parse
from datetime import datetime, timezone

from django.db.models import Count, Q

from ops.jobs import ServiceError, call

from .models import MatchPrediction

MLFLOW = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000").rstrip("/")
AIRFLOW = os.environ.get("AIRFLOW_API_URL", "http://airflow:8080").rstrip("/")
MODEL = "match-winner"  # MLflow 등록 모델 이름
PREDICT_DAG = "ml_daily_predict"  # 모델을 바꾼 뒤 예측을 다시 만들 DAG


# MLflow와 Airflow를 부르는 HTTP 도우미는 ops.jobs의 것을 쓴다. 관리자 화면과 테스트를 위해 이 이름으로도 둔다
RegistryError = ServiceError
_call = call


def _float(tags, key):
    """태그 값을 float로(없거나 숫자가 아니면 None)."""
    try:
        return float(tags[key])
    except (KeyError, TypeError, ValueError):
        return None


def live_records(name=MODEL):
    """{버전: (채점된 예측 수, 적중 수)}. 경기 전에 낸 예측만 세므로 결과를 보고 끼워 맞춘 것이 없다."""
    rows = (MatchPrediction.objects.filter(model_version__name=name, is_correct__isnull=False)
            .values("model_version__version")
            .annotate(n=Count("id"), hits=Count("id", filter=Q(is_correct=True))))
    return {r["model_version__version"]: (r["n"], r["hits"]) for r in rows}


def versions(name=MODEL):
    """등록된 버전들(최신부터): 별칭, 상태, 학습 시즌, 시험 성적(학습 작업이 버전 태그로 남긴 값), Elo 대비 우위,
    실제 경기 적중 기록. 관리자 '모델 관리' 화면의 표가 된다."""
    q = urllib.parse.urlencode({"name": name})
    aliases = {}
    for a in _call("GET", f"{MLFLOW}/api/2.0/mlflow/registered-models/get?{q}")["registered_model"].get("aliases", []):
        aliases.setdefault(int(a["version"]), []).append(a["alias"])
    q = urllib.parse.urlencode({"filter": f"name='{name}'", "max_results": 200})
    found = _call("GET", f"{MLFLOW}/api/2.0/mlflow/model-versions/search?{q}").get("model_versions", [])
    live = live_records(name)
    out = []
    for v in found:
        tags = {t["key"]: t["value"] for t in v.get("tags", [])}
        n = int(v["version"])
        acc, ll, elo_ll = _float(tags, "test_accuracy"), _float(tags, "test_log_loss"), _float(tags, "elo_test_log_loss")
        played, hits = live.get(n, (0, 0))
        out.append({
            "version": n, "aliases": sorted(aliases.get(n, [])), "status": v.get("status", ""),
            "created": datetime.fromtimestamp(int(v["creation_timestamp"]) / 1000, tz=timezone.utc),
            "run_id": v.get("run_id", ""), "train_seasons": tags.get("train_seasons", ""),
            "test_season": tags.get("test_season", ""), "data_cutoff": tags.get("data_cutoff", ""),
            "test_accuracy": acc, "test_log_loss": ll, "elo_test_log_loss": elo_ll,
            "beats_elo": ll is not None and elo_ll is not None and ll < elo_ll,
            "test_accuracy_pct": acc * 100 if acc is not None else None,
            "live_n": played, "live_hits": hits, "live_pct": hits * 100 / played if played else None,
        })
    return sorted(out, key=lambda v: -v["version"])


def champion(vs):
    """버전 목록에서 champion 별칭이 붙은 버전 번호(없으면 None)."""
    return next((v["version"] for v in vs if "champion" in v["aliases"]), None)


def set_champion(version, name=MODEL):
    """그 버전에 champion 별칭을 붙인다(이전 버전에서는 MLflow가 자동으로 떼어 간다)."""
    _call("POST", f"{MLFLOW}/api/2.0/mlflow/registered-models/alias",
          {"name": name, "alias": "champion", "version": str(version)})


def trigger_predictions(note):
    """ml_daily_predict를 지금 실행해 다가오는 경기 예측을 새 모델로 다시 만든다. 실행 id를 돌려준다."""
    run = _call("POST", f"{AIRFLOW}/api/v2/dags/{PREDICT_DAG}/dagRuns", {"logical_date": None, "note": note[:1000]})
    return run.get("dag_run_id", "")
