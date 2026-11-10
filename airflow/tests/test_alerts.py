"""airflow/dags/kovo_tasks/alerts.py(작업 실패 알림, PIPE-09)의 단위 테스트. Airflow·DB·네트워크 없이 돈다.

확인하는 것:
  - 알림 문구와 Airflow 화면 링크(task_url)가 올바르게 만들어지는지
  - 메신저(Mattermost/Slack/Discord)마다 웹훅 본문 형식이 맞는지
  - 보내기 실패가 예외 대신 결과 문자열로 돌아오는지
  - 같은 작업이 계속 실패할 때 두 번째부터는 조용히 기록만 하는지(REPEAT/COVERED)
  - DB가 죽어도 알림은 나가고, 콜백은 어떤 경우에도 예외를 던지지 않는지
DB 연결은 FakeConn/FakeCursor(메모리 목록)로, 웹훅 전송은 opener/sender 함수로 바꿔 끼운다.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

# 저장소 최상위 폴더. DAG 폴더와 수집기 패키지를 import 경로 앞에 넣어 Airflow 없이 모듈을 불러온다.
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "airflow" / "dags"), str(ROOT / "collectors")]

from kovo_tasks import alerts  # noqa: E402

# 테스트 공용 알림 한 건: spark-submit이 메모리 부족(exit 137)으로 4번째 시도에서 실패한 상황
ALERT = {"dag_id": "kovo_postgame", "task_id": "curate", "run_id": "scheduled__2026-11-06T10:40:00+00:00",
         "map_index": -1, "try_number": 4, "error": "CalledProcessError: spark-submit exit 137\nmore",
         "log_url": alerts.task_url("kovo_postgame", "scheduled__2026-11-06T10:40:00+00:00", "curate")}


def test_message_and_url():
    """링크의 run_id는 URL 인코딩되고, 문구에는 오류 첫 줄·시도 횟수·링크만 담긴다."""
    assert ALERT["log_url"].endswith("/dags/kovo_postgame/runs/scheduled__2026-11-06T10%3A40%3A00%2B00%3A00/tasks/curate")
    assert alerts.task_url("kovo_backfill", "r1", "one_season.curate", 3).endswith("?map_index=3")
    text = alerts.message(ALERT)
    assert "kovo_postgame › curate" in text and "spark-submit exit 137" in text and "more" not in text
    assert "4회째" in text and ALERT["log_url"] in text
    assert "(실행 전체)" in alerts.message({**ALERT, "task_id": "", "try_number": None})


def test_payload_per_messenger():
    """Mattermost·Slack은 {"text"}, Discord는 {"content"}로 보낸다(웹훅 주소로 구분)."""
    assert alerts.payload("https://mm.example.com/hooks/abc", "hi") == {"text": "hi"}
    assert alerts.payload("https://hooks.slack.com/services/x", "hi") == {"text": "hi"}
    assert alerts.payload("https://discord.com/api/webhooks/1/x", "hi") == {"content": "hi"}


class FakeResp:
    """urlopen이 돌려주는 응답 흉내: with 문에서 쓰이고 status만 있으면 된다."""
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send():
    """웹훅 주소가 없으면 NO_WEBHOOK, 성공하면 SENT 200, 네트워크 오류는 예외 대신 FAILED 문자열."""
    sent = []

    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return FakeResp()

    assert alerts.send("x", url="") == "NO_WEBHOOK"
    assert alerts.send("x", url="https://mm.example.com/hooks/abc", opener=opener) == "SENT 200"
    assert sent == [{"text": "x"}]

    def broken(req, timeout):
        raise OSError("refused")

    assert alerts.send("x", url="https://mm.example.com/hooks/abc", opener=broken).startswith("FAILED OSError")


class FakeCursor:
    """job_alert 테이블을 흉내 내는 커서. alerts.py가 보내는 SQL 네 종류만 알아듣는다.

    INSERT(새 알림 기록) / 같은 작업의 다른 OPEN 알림 찾기 / 같은 실행에 이미 알림이 있는지 / delivery 결과 갱신.
    """
    def __init__(self, db):
        self.db, self.result = db, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args):
        sql = " ".join(sql.split())
        if sql.startswith("INSERT"):
            self.db.rows.append({"id": len(self.db.rows) + 1, "dag_id": args[0], "task_id": args[1], "run_id": args[2],
                                 "delivery": "", "status": "OPEN"})
            self.result = (len(self.db.rows),)
        elif "status = 'OPEN'" in sql:
            dag, task, me, _ = args
            self.result = next(((1,) for r in self.db.rows if r["dag_id"] == dag and r["task_id"] == task
                                and r["status"] == "OPEN" and r["id"] != me), None)
        elif sql.startswith("SELECT 1 FROM job_alert WHERE dag_id = %s AND run_id"):
            self.result = next(((1,) for r in self.db.rows if (r["dag_id"], r["run_id"]) == args), None)
        elif sql.startswith("UPDATE"):
            self.db.rows[args[1] - 1]["delivery"] = args[0]

    def fetchone(self):
        return self.result


class FakeConn:
    """DB 연결 흉내. rows가 job_alert 테이블 역할을 한다."""
    def __init__(self):
        self.rows = []

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def test_notify_records_sends_and_quiets_repeats():
    """첫 실패만 메신저로 보내고, 해결 전 같은 작업의 실패는 REPEAT로 기록만 한다."""
    db, sent = FakeConn(), []
    sender = lambda text: sent.append(text) or "SENT 200"  # noqa: E731
    assert alerts.notify(ALERT, connect=lambda: db, sender=sender) == "SENT 200"
    assert alerts.notify({**ALERT, "run_id": "r2"}, connect=lambda: db, sender=sender).startswith("REPEAT")
    assert len(sent) == 1 and [r["delivery"][:6] for r in db.rows] == ["SENT 2", "REPEAT"]
    # DAG 단위 알림(task_id 없음)은 그 실행의 작업이 이미 알렸으면 COVERED로 조용히 넘어간다
    assert alerts.notify({**ALERT, "task_id": ""}, connect=lambda: db, sender=sender) == "COVERED"
    assert alerts.notify({**ALERT, "task_id": "", "run_id": "r3"}, connect=lambda: db, sender=sender) == "SENT 200"
    assert len(db.rows) == 3


def test_notify_without_db_still_sends():
    """기록용 DB에 연결할 수 없어도 알림 자체는 보낸다(알림이 DB 장애에 묻히지 않게)."""
    def down():
        raise OSError("db down")

    assert alerts.notify(ALERT, connect=down, sender=lambda text: "SENT 200") == "SENT 200"


def test_callbacks_never_raise(monkeypatch):
    """Airflow 실패 콜백은 context에서 정보를 꺼내 notify를 부르고, 잘못된 context에도 예외를 던지지 않는다."""
    seen = []
    monkeypatch.setattr(alerts, "notify", lambda alert: seen.append(alert) or "SENT 200")
    ti = SimpleNamespace(dag_id="kovo_daily", task_id="quality_gate", run_id="r1", map_index=-1, try_number=1)
    alerts.on_task_failure({"ti": ti, "exception": ValueError("2 open errors")})
    assert seen[0]["error"] == "ValueError: 2 open errors" and seen[0]["task_id"] == "quality_gate"
    alerts.on_dag_failure({"dag_run": SimpleNamespace(dag_id="kovo_daily", run_id="r1"), "reason": "task_failure"})
    assert seen[1]["task_id"] == "" and "task_failure" in seen[1]["error"]
    alerts.on_task_failure({})  # 잘못된 context: 로그만 남기고 예외는 던지지 않는다
