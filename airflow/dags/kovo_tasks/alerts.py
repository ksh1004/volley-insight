"""PIPE-09: 작업이 (재시도를 다 쓰고) 최종 실패하면 관리자에게 알린다.

모든 DAG가 `default_args(...)`(작업 단위 콜백)와 `on_dag_failure`(DAG 단위 콜백)를 단다:
  작업이 마지막 재시도까지 실패 → 서비스 DB `job_alert` 테이블에 한 행(Django 관리자 '수집 현황'에 보임)
                                 + ALERT_WEBHOOK_URL이 있으면 메신저(Mattermost/Slack/Discord) 메시지
  작업 알림 없이 DAG 실행이 실패(작업 프로세스가 강제 종료돼 자기 콜백을 못 부른 경우, 예: 메모리 부족)
                               → 실행 전체에 대해 한 행
같은 작업이 이전 알림이 아직 '미확인'인 상태로 REPEAT_QUIET_HOURS 안에 또 실패하면 기록만 하고 다시 보내지 않는다.
그래서 20분마다 실패하는 작업(kovo_postgame)이 채널을 도배하지 않는다.
콜백은 절대 예외를 던지지 않는다: 알림 경로가 고장 나도 Airflow 로그의 원래 실패 원인을 가리면 안 되기 때문이다.
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import timedelta

log = logging.getLogger(__name__)

WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL", "").strip()  # 메신저 웹훅 주소(.env, 비워 두면 DB 기록만)
AIRFLOW_UI = os.environ.get("AIRFLOW_UI_URL", "http://localhost:8080").rstrip("/")  # 알림에 넣을 로그 링크의 주소
REPEAT_QUIET_HOURS = 6  # 같은 작업의 반복 실패를 다시 보내지 않는 시간
ERROR_LIMIT = 2000  # 저장할 오류 문구의 최대 길이


def default_args(retries=3, retry_minutes=5) -> dict:
    """DAG의 default_args: 공통 재시도 규칙(설계서 2.3: 3번, 5분 간격)과 실패 알림 콜백."""
    return {"retries": retries, "retry_delay": timedelta(minutes=retry_minutes), "on_failure_callback": on_task_failure}


def task_url(dag_id, run_id, task_id="", map_index=-1) -> str:
    """Airflow 화면에서 그 실행(또는 작업)을 여는 링크. map_index는 동적 작업(expand)의 몇 번째인지."""
    url = f"{AIRFLOW_UI}/dags/{dag_id}/runs/{urllib.parse.quote(run_id, safe='')}"
    if task_id:
        url += f"/tasks/{task_id}" + (f"?map_index={map_index}" if map_index is not None and map_index >= 0 else "")
    return url


def error_text(exc) -> str:
    """예외를 "종류: 메시지" 문자열로(ERROR_LIMIT 글자까지)."""
    if exc is None:
        return ""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:ERROR_LIMIT]


def message(alert: dict) -> str:
    """메신저에 보낼 한국어 알림 문구(어느 작업, 어느 실행, 몇 번째 시도, 오류 첫 줄, 로그 링크)."""
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
    """웹훅 본문: Mattermost와 Slack은 {"text"}, Discord는 {"content"}(2000자 제한)를 받는다. 주소로 구별한다."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith(("discord.com", "discordapp.com")):
        return {"content": text[:2000]}
    return {"text": text}


def send(text: str, url: str = None, opener=urllib.request.urlopen) -> str:
    """웹훅으로 보내고, 알림 행에 함께 저장할 전달 결과 문구를 돌려준다(NO_WEBHOOK / SENT 200 / FAILED ...)."""
    url = WEBHOOK if url is None else url
    if not url:
        return "NO_WEBHOOK"
    req = urllib.request.Request(url, data=json.dumps(payload(url, text)).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "volley-insight-alerts"})
    try:
        with opener(req, timeout=10) as resp:
            return f"SENT {resp.status}"
    except Exception as e:  # noqa: BLE001 - 전달 오류는 기록만 하고 넘어간다
        return f"FAILED {type(e).__name__}: {str(e)[:150]}"


# ---------------------------------------------------------------- 서비스 DB (테이블은 Django가 관리: ops.JobAlert)
INSERT = """INSERT INTO job_alert (dag_id, task_id, run_id, map_index, try_number, error, log_url, delivery)
            VALUES (%s, %s, %s, %s, %s, %s, %s, '') RETURNING id"""
# 같은 작업의 다른 '미확인' 알림이 최근 N시간 안에 있는가
REPEATED = """SELECT 1 FROM job_alert WHERE dag_id = %s AND task_id = %s AND status = 'OPEN' AND id <> %s
              AND created_at >= now() - make_interval(hours => %s) LIMIT 1"""


def record(conn, alert: dict) -> tuple[int, bool]:
    """알림을 저장하고 (id, 반복 여부)를 돌려준다. 반복 = 같은 작업의 미확인 알림이 최근에 있음."""
    with conn.cursor() as cur:
        cur.execute(INSERT, (alert["dag_id"], alert["task_id"], alert["run_id"], alert.get("map_index", -1),
                             alert.get("try_number"), alert.get("error", ""), alert["log_url"]))
        alert_id = cur.fetchone()[0]
        cur.execute(REPEATED, (alert["dag_id"], alert["task_id"], alert_id, REPEAT_QUIET_HOURS))
        return alert_id, cur.fetchone() is not None


def run_has_alert(conn, dag_id: str, run_id: str) -> bool:
    """이 실행에 대해 이미 (작업 단위) 알림이 있는가."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM job_alert WHERE dag_id = %s AND run_id = %s LIMIT 1", (dag_id, run_id))
        return cur.fetchone() is not None


def set_delivery(conn, alert_id: int, delivery: str):
    """알림 행에 전달 결과를 적는다."""
    with conn.cursor() as cur:
        cur.execute("UPDATE job_alert SET delivery = %s WHERE id = %s", (delivery[:200], alert_id))


def notify(alert: dict, connect=None, sender=send) -> str:
    """기록한 뒤, 최근 미확인 알림의 반복이 아니면 보낸다. DB에 접속할 수 없으면 기록 없이 보내기만 한다.
    DAG 단위 알림(task_id 없음)은 그 실행에 작업 알림이 이미 있으면 보내지 않는다(COVERED: 중복 방지)."""
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
                return "COVERED"  # 실패한 작업이 이미 이 실행에 대해 알렸다
            alert_id, repeated = record(conn, alert)
        delivery = f"REPEAT (같은 작업의 미확인 알림이 {REPEAT_QUIET_HOURS}시간 안에 있음)" if repeated else sender(text)
        with conn:
            set_delivery(conn, alert_id, delivery)
        return delivery
    finally:
        conn.close()


# ---------------------------------------------------------------- Airflow 콜백
def on_task_failure(context):
    """작업이 최종 실패했을 때 Airflow가 부른다(context에 작업 인스턴스 ti와 예외가 들어 있다)."""
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
    """DAG 실행이 실패했을 때 Airflow가 부른다. 작업 알림이 없을 때만 실제로 기록·전송된다(notify 참고)."""
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
