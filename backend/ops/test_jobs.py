"""Job status (ADM-01), failure alerts (PIPE-09) and manual runs (ADM-02). Airflow and Elasticsearch are mocked."""
from datetime import datetime, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from matches.tests import make_league

from . import jobs
from .models import JobAlert, ManualRun

DAG_ROWS = [{"dag_id": "kovo_postgame", "label": "경기 후 기록", "what": "x", "run_now": True, "url": "u", "missing": False,
             "paused": False, "schedule": "At every 20th minute", "next_run": None, "import_errors": False,
             "runs": [], "dots": [{"state": "failed", "state_label": "실패", "run_type": "scheduled", "url": "u",
                                   "run_after": None}],
             "last": None, "failed_7d": 2}]
COLLECTION = {"days": 7, "jobs": [{"job": "kovo.match", "success": 3, "failed": 1, "stuck": 0, "records": 12,
                                   "last": None, "last_failure": None}],
              "failures": [{"job": "kovo.match", "status": "FAILED", "target": "023-9", "error": "HTTP 503",
                            "started": None}]}


class JobStatusTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")
        JobAlert.objects.create(dag_id="kovo_postgame", task_id="curate", run_id="r1", try_number=4,
                                error="CalledProcessError: exit 137", log_url="http://localhost:8080/x",
                                delivery="NO_WEBHOOK")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_status_page_shows_jobs_collectors_and_failures(self):
        with mock.patch("ops.jobs.overview", return_value=DAG_ROWS), \
             mock.patch("ops.jobs.collection_runs", return_value=COLLECTION):
            res = self.client.get(reverse("admin:ops_jobalert_changelist"))
        self.assertContains(res, "경기 후 기록")
        self.assertContains(res, "kovo.match")
        self.assertContains(res, "HTTP 503")
        self.assertContains(res, "CalledProcessError: exit 137")
        self.assertContains(res, "확인하지 않은 작업 실패가 1건")
        self.assertContains(res, "ALERT_WEBHOOK_URL")  # the last alert could not be sent anywhere

    def test_status_page_survives_airflow_and_elasticsearch_down(self):
        with mock.patch("ops.jobs.overview", side_effect=jobs.ServiceError("Airflow 꺼짐")), \
             mock.patch("ops.jobs.collection_runs", side_effect=ConnectionError("es down")):
            res = self.client.get(reverse("admin:ops_jobalert_changelist"))
        self.assertContains(res, "Airflow를 읽지 못했습니다: Airflow 꺼짐")
        self.assertContains(res, "수집 기록을 읽지 못했습니다")

    def test_admin_index_notice_and_acknowledge(self):
        self.assertContains(self.client.get(reverse("admin:index")), "확인하지 않은 작업 실패가 1건")
        alert = JobAlert.objects.get()
        with mock.patch("ops.jobs.overview", return_value=[]), mock.patch("ops.jobs.collection_runs", return_value=None):
            self.client.post(reverse("admin:ops_jobalert_changelist"),
                             {"action": "acknowledge", "_selected_action": [alert.pk]})
        alert.refresh_from_db()
        self.assertEqual((alert.status, alert.acknowledged_by), (JobAlert.Status.ACKNOWLEDGED, self.admin))
        self.assertNotContains(self.client.get(reverse("admin:index")), "확인하지 않은 작업 실패")

    def test_collection_runs_pairs_start_and_finish(self):
        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)

        def doc(rid, status, started, job="kovo.match", **kw):
            return {"_source": {"collection": {"run_id": rid, "job_name": job, "status": status,
                                               "started_at": started, **kw}}}

        hits = [  # newest first, like the query
            doc("a", "SUCCESS", "2026-11-06T11:00:00+00:00", finished_at="2026-11-06T11:01:00+00:00", record_count=5),
            doc("a", "RUNNING", "2026-11-06T11:00:00+00:00"),
            doc("b", "FAILED", "2026-11-06T10:00:00+00:00", finished_at="2026-11-06T10:00:30+00:00",
                record_count=0, error_message="HTTP 503"),
            doc("b", "RUNNING", "2026-11-06T10:00:00+00:00"),
            doc("c", "RUNNING", "2026-11-06T05:00:00+00:00"),                       # never finished: stuck
            doc("d", "RUNNING", "2026-11-06T11:50:00+00:00", job="news_collect"),   # still running: not stuck
        ]
        es = mock.Mock()
        es.search.return_value = {"hits": {"hits": hits}}
        out = jobs.collection_runs(es=es, now=now)
        match = next(j for j in out["jobs"] if j["job"] == "kovo.match")
        self.assertEqual((match["success"], match["failed"], match["stuck"], match["records"]), (1, 1, 1, 5))
        self.assertEqual(match["last"]["count"], 5)
        self.assertEqual(match["last_failure"]["error"], "HTTP 503")
        self.assertEqual([f["status"] for f in out["failures"]], ["FAILED", "RUNNING"])
        news = next(j for j in out["jobs"] if j["job"] == "news_collect")
        self.assertEqual((news["success"], news["stuck"], news["last"]), (0, 0, None))

    def test_overview_reads_airflow(self):
        def fake(method, url, body=None, timeout=10):
            if url.endswith("/api/v2/dags?limit=100"):
                return {"dags": [{"dag_id": "kovo_daily", "is_paused": False, "timetable_description": "At 06:00",
                                  "next_dagrun_run_after": "2026-11-06T21:00:00Z"}]}
            if "state=failed" in url:
                return {"total_entries": 1}
            return {"dag_runs": [
                {"dag_run_id": "s2", "dag_id": "kovo_daily", "state": "failed", "run_type": "scheduled",
                 "start_date": "2026-11-06T01:00:00Z", "end_date": "2026-11-06T01:06:00Z", "run_after": "2026-11-05T21:00:00Z"},
                {"dag_run_id": "s1", "dag_id": "kovo_daily", "state": "success", "run_type": "scheduled",
                 "start_date": "2026-11-05T01:00:00Z", "end_date": "2026-11-05T01:03:00Z", "run_after": "2026-11-04T21:00:00Z"}]}

        with mock.patch("ops.jobs.call", side_effect=fake):
            rows = jobs.overview()
        daily = rows[0]
        self.assertEqual(daily["dag_id"], "kovo_daily")
        self.assertEqual((daily["last"]["state"], daily["last"]["minutes"], daily["failed_7d"]), ("failed", 6.0, 1))
        self.assertEqual([r["run_id"] for r in daily["dots"]], ["s1", "s2"])  # oldest first
        self.assertTrue(all(r["missing"] for r in rows[1:]))                    # not in Airflow's answer


class ManualRunTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")

    def setUp(self):
        self.client.force_login(self.admin)

    def run_form(self, kind, **fields):
        data = {"kind": kind, **{f"{kind}-{k}": v for k, v in fields.items()}}
        with mock.patch("ops.jobs.trigger", return_value="manual__1") as trigger:
            res = self.client.post(reverse("admin:ops_manualrun_run"), data)
        return res, trigger

    def test_reprocess_games_and_dates(self):
        res, trigger = self.run_form("reprocess", games="023-9, 023-10", dates="2026-11-01~2026-11-05",
                                     recollect="on", note="KOVO 기록 정정")
        self.assertRedirects(res, reverse("admin:ops_manualrun_changelist"), fetch_redirect_response=False)
        dag, conf, note = trigger.call_args.args
        self.assertEqual(dag, "kovo_reprocess")
        self.assertEqual(conf, {"games": "023-9, 023-10", "dates": "2026-11-01~2026-11-05", "seasons": "",
                                "recollect": True})
        self.assertIn("KOVO 기록 정정", note)
        run = ManualRun.objects.get()
        self.assertEqual((run.dag_id, run.run_id, run.requested_by), ("kovo_reprocess", "manual__1", self.admin))
        self.assertContains(self.client.get(reverse("admin:ops_manualrun_changelist")), "games=023-9, 023-10")

    def test_bad_input_is_shown_and_not_sent(self):
        cases = [("reprocess", {}), ("reprocess", {"games": "23번"}), ("reprocess", {"dates": "2026-11-05~2026-10-01"}),
                 ("backfill", {"seasons": "all"}), ("replay", {"match": "019-3", "speed": "10"}),
                 ("replay", {"match": "022-99", "speed": "10"}), ("replay", {"match": "022-3", "speed": "0"}),
                 ("now", {"dag": "kovo_backfill"})]
        for kind, fields in cases:
            res, trigger = self.run_form(kind, **fields)
            self.assertContains(res, "입력을 확인해 주세요", msg_prefix=str((kind, fields)))
            self.assertContains(res, "errorlist", msg_prefix=str((kind, fields)))
            trigger.assert_not_called()
        self.assertFalse(ManualRun.objects.exists())

    def test_run_now_replay_and_backfill_configs(self):
        _, t = self.run_form("now", dag="kovo_postgame")
        self.assertEqual(t.call_args.args[:2], ("kovo_postgame", {}))
        _, t = self.run_form("replay", match="022-3", speed="20")
        self.assertEqual(t.call_args.args[:2], ("live_replay", {"match": "022-3", "speed": 20.0}))
        _, t = self.run_form("backfill", seasons="019,020")
        self.assertEqual(t.call_args.args[:2], ("kovo_backfill", {"seasons": "019,020", "limit": None,
                                                                  "skip_confirmed": False}))

    def test_airflow_error_and_permission(self):
        with mock.patch("ops.jobs.trigger", side_effect=jobs.ServiceError("일시 중지")):
            res = self.client.post(reverse("admin:ops_manualrun_run"), {"kind": "now", "now-dag": "kovo_daily"},
                                   follow=True)
        self.assertContains(res, "실행하지 못했습니다: 일시 중지")
        self.assertFalse(ManualRun.objects.exists())
        staff = get_user_model().objects.create_user("staff", "s@example.com", "pw-for-tests-1", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename="view_manualrun"))
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("admin:ops_manualrun_changelist")), "실행할 권한이 없습니다")
        _, trigger = self.run_form("now", dag="kovo_daily")
        trigger.assert_not_called()

    def test_page_renders_all_forms(self):
        res = self.client.get(reverse("admin:ops_manualrun_changelist"))
        for text in ("예약 작업 지금 실행", "경기·날짜·시즌 재처리", "과거 시즌 수집", "경기 재생", 'name="reprocess-games"'):
            self.assertContains(res, text)
