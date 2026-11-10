"""ADM-01 작업 현황과 ADM-02 수동 실행: Airflow REST API에서 읽고, Airflow REST API로 실행을 요청한다.

수집기 실행 기록(COL-08)은 Elasticsearch `ops-collection-*`에서 읽는다(Kafka ops.collection-log → Filebeat → ES).
두 서비스 모두 꺼져 있을 수 있다(compose의 batch/search 프로필). 그러면 관리자 화면이 오류로 죽지 않고 그렇다고 알린다.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

AIRFLOW = os.environ.get("AIRFLOW_API_URL", "http://airflow:8080").rstrip("/")
# 관리자가 같은 PC의 브라우저로 Airflow를 연다(Airflow는 127.0.0.1에만 열려 있음, NFR-34). 링크 주소용
AIRFLOW_UI = os.environ.get("AIRFLOW_UI_URL", "http://localhost:8080").rstrip("/")
COLLECTION_INDEX = "ops-collection-*"  # 수집 실행 로그 색인
RECENT_RUNS = 12  # DAG마다 보여 줄 최근 실행 수
STUCK_AFTER = timedelta(hours=3)  # 시작만 하고 이만큼 결과가 없으면 '멈춘 실행'(프로세스가 죽은 것)으로 본다


class ServiceError(Exception):
    """Airflow·MLflow 같은 외부 서비스 호출이 실패했을 때. 메시지는 관리자 화면에 그대로 보인다(한국어)."""


def call(method, url, body=None, timeout=10):
    """JSON REST API를 부르는 작은 HTTP 도우미(요청 라이브러리 없이 표준 라이브러리만). 실패하면 ServiceError."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode() or "{}"
    except urllib.error.HTTPError as e:
        raise ServiceError(f"{url.split('/api/')[0]} 응답 {e.code}: {e.read().decode()[:200]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServiceError(f"{url.split('/api/')[0]}에 연결하지 못했습니다 (batch 프로필이 꺼져 있나요?)") from e
    return json.loads(text)


# 우리 DAG들(화면에 보이는 순서). label/what은 화면 문구, `run_now`: 수동 실행 화면에서 설정 없이 바로 실행해도 되는가
DAGS = {
    "kovo_daily": {"label": "매일 수집·정제", "what": "일정·결과, 최근 7일 누락 경기 → 정제 → 마트 → 품질 검사", "run_now": True},
    "kovo_postgame": {"label": "경기 후 기록", "what": "막 끝난 경기의 선수 기록·문자중계 → 정제 → 기록 확정", "run_now": True},
    "news_collect": {"label": "기사 수집", "what": "스포츠 RSS(+네이버 API) → 기사 저장·색인", "run_now": True},
    "ml_daily_predict": {"label": "경기 예측", "what": "서비스 모델로 7일 안 경기 승리 확률", "run_now": True},
    "ml_weekly_retrain": {"label": "모델 재학습", "what": "새 설정 탐색·학습 → 더 나으면 서비스 모델 교체", "run_now": True},
    "kovo_reprocess": {"label": "재처리", "what": "고른 경기·날짜·시즌을 다시 수집하거나 원천부터 다시 정제", "run_now": False},
    "kovo_backfill": {"label": "과거 시즌 수집", "what": "시즌 전체 수집·정제 (시즌당 약 20분)", "run_now": False},
    "live_replay": {"label": "경기 재생", "what": "끝난 경기를 실시간 파이프라인으로 다시 보냄 (시연·점검)", "run_now": False},
}
# Airflow 실행 상태 → 화면 문구
STATE_LABELS = {"success": "성공", "failed": "실패", "running": "실행 중", "queued": "대기", "skipped": "건너뜀"}


def _time(text):
    """Airflow의 ISO 시각 문자열 → datetime(없으면 None)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def run_url(dag_id, run_id=""):
    """Airflow 화면에서 그 DAG(또는 실행)를 여는 링크."""
    url = f"{AIRFLOW_UI}/dags/{dag_id}"
    return url + (f"/runs/{urllib.parse.quote(run_id, safe='')}" if run_id else "")


def _run(r):
    """Airflow 실행 정보를 화면용으로: 상태 문구, 시작·끝, 걸린 분, 링크, 실행 설정."""
    start, end = _time(r.get("start_date")), _time(r.get("end_date"))
    return {"run_id": r["dag_run_id"], "state": r.get("state") or "", "state_label": STATE_LABELS.get(r.get("state"), r.get("state")),
            "run_type": r.get("run_type", ""), "start": start, "end": end,
            "minutes": round((end - start).total_seconds() / 60, 1) if start and end else None,
            "run_after": _time(r.get("run_after")), "url": run_url(r["dag_id"], r["dag_run_id"]),
            "conf": r.get("conf") or {}}


def overview(now=None):
    """DAG마다 한 줄: 일시 중지 여부, 일정, 다음 실행, 최근 실행들(최신부터), 최근 7일 실패 수. '수집 현황' 화면의 표."""
    now = now or datetime.now(timezone.utc)
    dags = {d["dag_id"]: d for d in call("GET", f"{AIRFLOW}/api/v2/dags?limit=100")["dags"]}
    week = urllib.parse.quote((now - timedelta(days=7)).isoformat())
    rows = []
    for dag_id, meta in DAGS.items():
        d = dags.get(dag_id)
        row = {"dag_id": dag_id, **meta, "url": run_url(dag_id), "missing": d is None, "runs": [], "dots": [], "last": None,
               "failed_7d": 0}
        if d is not None:
            q = urllib.parse.urlencode({"order_by": "-run_after", "limit": RECENT_RUNS})
            runs = [_run(r) for r in call("GET", f"{AIRFLOW}/api/v2/dags/{dag_id}/dagRuns?{q}")["dag_runs"]]
            failed = call("GET", f"{AIRFLOW}/api/v2/dags/{dag_id}/dagRuns?state=failed&run_after_gte={week}&limit=1")
            row.update(paused=d.get("is_paused", False), schedule=d.get("timetable_description") or "",
                       next_run=_time(d.get("next_dagrun_run_after")), import_errors=d.get("has_import_errors"),
                       runs=runs, last=runs[0] if runs else None, failed_7d=failed.get("total_entries", 0),
                       dots=list(reversed(runs)))
        rows.append(row)
    return rows


def dag_info(dag_id):
    """DAG 하나의 정보(일시 중지 여부 등)."""
    return call("GET", f"{AIRFLOW}/api/v2/dags/{urllib.parse.quote(dag_id)}")


def trigger(dag_id, conf, note):
    """이 설정으로 DAG 실행을 지금 시작하고 실행 id를 돌려준다. 일시 중지된 DAG는 거부한다(대기열에만 쌓이므로)."""
    if dag_info(dag_id).get("is_paused"):
        raise ServiceError(f"{dag_id}이(가) Airflow에서 일시 중지되어 있어 실행되지 않습니다. Airflow 화면에서 켜 주세요.")
    run = call("POST", f"{AIRFLOW}/api/v2/dags/{urllib.parse.quote(dag_id)}/dagRuns",
               {"logical_date": None, "conf": conf, "note": note[:1000]})
    return run.get("dag_run_id", "")


# ---------------------------------------------------------------- 수집기 실행 기록 (Elasticsearch)
def collection_runs(es=None, days=7, now=None):
    """최근 `days`일의 수집기 실행을 작업별로 묶는다: 마지막으로 끝난 실행, 성공·실패·멈춤 수, 모은 레코드 수,
    최근 실패와 '시작만 하고 결과를 못 남긴 실행'(프로세스가 죽은 경우).
    한 실행은 시작(RUNNING)과 끝(SUCCESS/FAILED) 두 문서가 있으므로 run_id로 합쳐 끝 문서를 우선한다."""
    from articles.search import client

    now = now or datetime.now(timezone.utc)
    es = es or client()
    res = es.search(index=COLLECTION_INDEX, size=2000, sort=[{"@timestamp": "desc"}], _source=["collection"],
                    query={"range": {"@timestamp": {"gte": f"now-{days}d"}}}, ignore_unavailable=True)
    runs = {}
    for hit in res["hits"]["hits"]:
        c = hit["_source"].get("collection") or {}
        rid = c.get("run_id")
        if not rid:
            continue
        cur = runs.get(rid)
        if cur is None or (cur["status"] == "RUNNING" and c.get("status") != "RUNNING"):
            runs[rid] = {"job": c.get("job_name", ""), "status": c.get("status", ""), "target": c.get("target") or "",
                         "count": c.get("record_count"), "error": c.get("error_message") or "",
                         "started": _time(c.get("started_at")), "finished": _time(c.get("finished_at"))}
    jobs = {}
    for r in sorted(runs.values(), key=lambda r: r["started"] or now, reverse=True):
        j = jobs.setdefault(r["job"], {"job": r["job"], "success": 0, "failed": 0, "stuck": 0, "records": 0,
                                       "last": None, "last_failure": None})
        if r["status"] == "RUNNING":
            if r["started"] and now - r["started"] > STUCK_AFTER:
                j["stuck"] += 1
            continue
        j["success" if r["status"] == "SUCCESS" else "failed"] += 1
        j["records"] += r["count"] or 0
        j["last"] = j["last"] or r
        if r["status"] != "SUCCESS":
            j["last_failure"] = j["last_failure"] or r
    failures = [r for r in runs.values() if r["status"] == "FAILED"
                or (r["status"] == "RUNNING" and r["started"] and now - r["started"] > STUCK_AFTER)]
    failures.sort(key=lambda r: r["started"] or now, reverse=True)
    return {"jobs": sorted(jobs.values(), key=lambda j: j["job"]), "failures": failures[:20], "days": days}
