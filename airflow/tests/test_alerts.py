"""Unit tests for airflow/dags/kovo_tasks/alerts.py (PIPE-09). No Airflow, database or network needed."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "airflow" / "dags"), str(ROOT / "collectors")]

from kovo_tasks import alerts  # noqa: E402

ALERT = {"dag_id": "kovo_postgame", "task_id": "curate", "run_id": "scheduled__2026-11-06T10:40:00+00:00",
         "map_index": -1, "try_number": 4, "error": "CalledProcessError: spark-submit exit 137\nmore",
         "log_url": alerts.task_url("kovo_postgame", "scheduled__2026-11-06T10:40:00+00:00", "curate")}


def test_message_and_url():
    assert ALERT["log_url"].endswith("/dags/kovo_postgame/runs/scheduled__2026-11-06T10%3A40%3A00%2B00%3A00/tasks/curate")
    assert alerts.task_url("kovo_backfill", "r1", "one_season.curate", 3).endswith("?map_index=3")
    text = alerts.message(ALERT)
    assert "kovo_postgame › curate" in text and "spark-submit exit 137" in text and "more" not in text
    assert "4회째" in text and ALERT["log_url"] in text
    assert "(실행 전체)" in alerts.message({**ALERT, "task_id": "", "try_number": None})


def test_payload_per_messenger():
    assert alerts.payload("https://mm.example.com/hooks/abc", "hi") == {"text": "hi"}
    assert alerts.payload("https://hooks.slack.com/services/x", "hi") == {"text": "hi"}
    assert alerts.payload("https://discord.com/api/webhooks/1/x", "hi") == {"content": "hi"}


class FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send():
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
    db, sent = FakeConn(), []
    sender = lambda text: sent.append(text) or "SENT 200"  # noqa: E731
    assert alerts.notify(ALERT, connect=lambda: db, sender=sender) == "SENT 200"
    assert alerts.notify({**ALERT, "run_id": "r2"}, connect=lambda: db, sender=sender).startswith("REPEAT")
    assert len(sent) == 1 and [r["delivery"][:6] for r in db.rows] == ["SENT 2", "REPEAT"]
    # the DAG-level fallback stays quiet when a task of that run already alerted
    assert alerts.notify({**ALERT, "task_id": ""}, connect=lambda: db, sender=sender) == "COVERED"
    assert alerts.notify({**ALERT, "task_id": "", "run_id": "r3"}, connect=lambda: db, sender=sender) == "SENT 200"
    assert len(db.rows) == 3


def test_notify_without_db_still_sends():
    def down():
        raise OSError("db down")

    assert alerts.notify(ALERT, connect=down, sender=lambda text: "SENT 200") == "SENT 200"


def test_callbacks_never_raise(monkeypatch):
    seen = []
    monkeypatch.setattr(alerts, "notify", lambda alert: seen.append(alert) or "SENT 200")
    ti = SimpleNamespace(dag_id="kovo_daily", task_id="quality_gate", run_id="r1", map_index=-1, try_number=1)
    alerts.on_task_failure({"ti": ti, "exception": ValueError("2 open errors")})
    assert seen[0]["error"] == "ValueError: 2 open errors" and seen[0]["task_id"] == "quality_gate"
    alerts.on_dag_failure({"dag_run": SimpleNamespace(dag_id="kovo_daily", run_id="r1"), "reason": "task_failure"})
    assert seen[1]["task_id"] == "" and "task_failure" in seen[1]["error"]
    alerts.on_task_failure({})  # malformed context: logged, not raised
