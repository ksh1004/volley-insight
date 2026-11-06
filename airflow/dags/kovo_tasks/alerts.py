"""PIPE-09: tell the admin when a job has failed for good (after its retries).

Every DAG passes `default_args(...)` (task-level callback) and `on_dag_failure` (DAG-level callback):
  task failed after its last retry -> one row in the service DB table `job_alert` (Django admin "수집 현황"),
                                      plus a messenger message when ALERT_WEBHOOK_URL is set
  DAG run failed without a task alert (the task process was killed, e.g. out of memory, so its own callback
  never ran) -> one row for the whole run
The same task failing again within REPEAT_QUIET_HOURS while its earlier alert is still open is recorded but not
sent again, so a job that fails every 20 minutes (kovo_postgame) does not flood the channel.
A callback never raises: a broken alert path must not hide the original failure in the Airflow log.
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import timedelta

log = logging.getLogger(__name__)

WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
AIRFLOW_UI = os.environ.get("AIRFLOW_UI_URL", "http://localhost:8080").rstrip("/")
REPEAT_QUIET_HOURS = 6
ERROR_LIMIT = 2000


def default_args(retries=3, retry_minutes=5) -> dict:
    """DAG default_args with the shared retry policy (design.md 2.3: 3 retries, 5 minutes apart) and the alert."""
    return {"retries": retries, "retry_delay": timedelta(minutes=retry_minutes), "on_failure_callback": on_task_failure}


def task_url(dag_id, run_id, task_id="", map_index=-1) -> str:
    url = f"{AIRFLOW_UI}/dags/{dag_id}/runs/{urllib.parse.quote(run_id, safe='')}"
    if task_id:
        url += f"/tasks/{task_id}" + (f"?map_index={map_index}" if map_index is not None and map_index >= 0 else "")
    return url


def error_text(exc) -> str:
    if exc is None:
        return ""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:ERROR_LIMIT]


def message(alert: dict) -> str:
    where = alert["dag_id"] + (f" › {alert['task_id']}" if alert["task_id"] else " (실행 전체)")
    lines = [f":rotating_light: [배구 분석] 작업 실패: {where}", f"실행: {alert['run_id']}"]
    if alert.get("try_number"):
        lines.append(f"시도: {alert['try_number']}회째 (재시도를 모두 썼습니다)")
    first = (alert.get("error") or "").strip().splitlines()
    if first:
        lines.append(f"오류: {first[0][:300]}")
    lines.append(f"로그: {alert['log_url']}")
    return "\n".join(lines)


def payload(url: str, text: str) -> dict:
    """Mattermost and Slack incoming webhooks take {"text"}; Discord takes {"content"}."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith(("discord.com", "discordapp.com")):
        return {"content": text[:2000]}
    return {"text": text}


def send(text: str, url: str = None, opener=urllib.request.urlopen) -> str:
    """Post to the webhook; returns the delivery note stored with the alert."""
    url = WEBHOOK if url is None else url
    if not url:
        return "NO_WEBHOOK"
    req = urllib.request.Request(url, data=json.dumps(payload(url, text)).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "volley-insight-alerts"})
    try:
        with opener(req, timeout=10) as resp:
            return f"SENT {resp.status}"
    except Exception as e:  # noqa: BLE001 - any delivery error is only noted
        return f"FAILED {type(e).__name__}: {str(e)[:150]}"


# ---------------------------------------------------------------- service DB (table owned by Django: ops.JobAlert)
INSERT = """INSERT INTO job_alert (dag_id, task_id, run_id, map_index, try_number, error, log_url, delivery)
            VALUES (%s, %s, %s, %s, %s, %s, %s, '') RETURNING id"""
REPEATED = """SELECT 1 FROM job_alert WHERE dag_id = %s AND task_id = %s AND status = 'OPEN' AND id <> %s
              AND created_at >= now() - make_interval(hours => %s) LIMIT 1"""


def record(conn, alert: dict) -> tuple[int, bool]:
    """Insert the alert; returns (id, repeated) where repeated = an open alert of the same task is recent."""
    with conn.cursor() as cur:
        cur.execute(INSERT, (alert["dag_id"], alert["task_id"], alert["run_id"], alert.get("map_index", -1),
                             alert.get("try_number"), alert.get("error", ""), alert["log_url"]))
        alert_id = cur.fetchone()[0]
        cur.execute(REPEATED, (alert["dag_id"], alert["task_id"], alert_id, REPEAT_QUIET_HOURS))
        return alert_id, cur.fetchone() is not None


def run_has_alert(conn, dag_id: str, run_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM job_alert WHERE dag_id = %s AND run_id = %s LIMIT 1", (dag_id, run_id))
        return cur.fetchone() is not None


def set_delivery(conn, alert_id: int, delivery: str):
    with conn.cursor() as cur:
        cur.execute("UPDATE job_alert SET delivery = %s WHERE id = %s", (delivery[:200], alert_id))


def notify(alert: dict, connect=None, sender=send) -> str:
    """Record, then send unless it repeats a recent open alert. Falls back to sending only if the DB is down."""
    if connect is None:
        from kovo_tasks.pipeline import db_connect as connect
    text = message(alert)
    try:
        conn = connect()
    except Exception:  # noqa: BLE001
        log.exception("job_alert: service DB unavailable, sending without a record")
        return sender(text)
    try:
        with conn:
            if not alert["task_id"] and run_has_alert(conn, alert["dag_id"], alert["run_id"]):
                return "COVERED"  # the failed task already alerted for this run
            alert_id, repeated = record(conn, alert)
        delivery = f"REPEAT (같은 작업의 미확인 알림이 {REPEAT_QUIET_HOURS}시간 안에 있음)" if repeated else sender(text)
        with conn:
            set_delivery(conn, alert_id, delivery)
        return delivery
    finally:
        conn.close()


# ---------------------------------------------------------------- Airflow callbacks
def on_task_failure(context):
    try:
        ti = context["ti"]
        map_index = getattr(ti, "map_index", -1)
        map_index = -1 if map_index is None else map_index
        alert = {"dag_id": ti.dag_id, "task_id": ti.task_id, "run_id": ti.run_id, "map_index": map_index,
                 "try_number": getattr(ti, "try_number", None), "error": error_text(context.get("exception")),
                 "log_url": task_url(ti.dag_id, ti.run_id, ti.task_id, map_index)}
        log.warning("job alert %s: %s", alert["log_url"], notify(alert))
    except Exception:  # noqa: BLE001
        log.exception("job alert failed")


def on_dag_failure(context):
    try:
        run = context["dag_run"]
        reason = context.get("reason") or ""
        alert = {"dag_id": run.dag_id, "task_id": "", "run_id": run.run_id, "map_index": -1, "try_number": None,
                 "error": f"실행 실패 ({reason}). 작업이 강제 종료되었을 수 있습니다(메모리 부족 등). 실패한 작업의 로그를 확인하세요."
                 if reason else "실행 실패. 실패한 작업의 로그를 확인하세요.",
                 "log_url": task_url(run.dag_id, run.run_id)}
        log.warning("job alert %s: %s", alert["log_url"], notify(alert))
    except Exception:  # noqa: BLE001
        log.exception("job alert failed")
