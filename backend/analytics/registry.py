"""ADM-05: model versions from the MLflow registry and switching the serving model.

Uses MLflow's REST API (the web image has no mlflow package) and Airflow's REST API to re-run the predictions.
Both services are in the `batch` compose profile; when they are down the admin page says so instead of failing.
"""
import os
import urllib.parse
from datetime import datetime, timezone

from django.db.models import Count, Q

from ops.jobs import ServiceError, call

from .models import MatchPrediction

MLFLOW = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000").rstrip("/")
AIRFLOW = os.environ.get("AIRFLOW_API_URL", "http://airflow:8080").rstrip("/")
MODEL = "match-winner"
PREDICT_DAG = "ml_daily_predict"


# one HTTP helper for MLflow and Airflow (ops.jobs); kept under these names for the admin and the tests
RegistryError = ServiceError
_call = call


def _float(tags, key):
    try:
        return float(tags[key])
    except (KeyError, TypeError, ValueError):
        return None


def live_records(name=MODEL):
    """{version: (finished predictions, correct)} — pre-match predictions only, so nothing is back-fitted."""
    rows = (MatchPrediction.objects.filter(model_version__name=name, is_correct__isnull=False)
            .values("model_version__version")
            .annotate(n=Count("id"), hits=Count("id", filter=Q(is_correct=True))))
    return {r["model_version__version"]: (r["n"], r["hits"]) for r in rows}


def versions(name=MODEL):
    """Registered versions, newest first, with the test metrics the training job stored as version tags."""
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
    return next((v["version"] for v in vs if "champion" in v["aliases"]), None)


def set_champion(version, name=MODEL):
    _call("POST", f"{MLFLOW}/api/2.0/mlflow/registered-models/alias",
          {"name": name, "alias": "champion", "version": str(version)})


def trigger_predictions(note):
    """Start ml_daily_predict now so upcoming matches get the new model's predictions; returns the run id."""
    run = _call("POST", f"{AIRFLOW}/api/v2/dags/{PREDICT_DAG}/dagRuns", {"logical_date": None, "note": note[:1000]})
    return run.get("dag_run_id", "")
