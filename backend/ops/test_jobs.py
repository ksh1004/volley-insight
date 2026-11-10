"""관리자 화면의 작업 현황(ADM-01), 실패 알림(PIPE-09), 수동 실행(ADM-02) 테스트.

  docker compose exec -T django-api python manage.py test ops

Airflow REST API와 Elasticsearch 호출은 mock으로 바꿔 끼워 실제 서비스 없이 돈다.
관리자 계정은 테스트 DB에만 만들어진다(실제 DB에는 아무것도 만들지 않음).
"""
from datetime import datetime, timezone
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from matches.tests import make_league

from . import jobs
from .models import JobAlert, ManualRun

# jobs.overview()가 돌려주는 DAG 현황 예시: 경기 후 기록 DAG, 최근 실행 1번 실패, 7일간 실패 2번
DAG_ROWS = [{"dag_id": "kovo_postgame", "label": "경기 후 기록", "what": "x", "run_now": True, "url": "u", "missing": False,
             "paused": False, "schedule": "At every 20th minute", "next_run": None, "import_errors": False,
             "runs": [], "dots": [{"state": "failed", "state_label": "실패", "run_type": "scheduled", "url": "u",
                                   "run_after": None}],
             "last": None, "failed_7d": 2}]
# jobs.collection_runs()가 돌려주는 수집 기록 요약 예시: kovo.match 성공 3, 실패 1(HTTP 503)
COLLECTION = {"days": 7, "jobs": [{"job": "kovo.match", "success": 3, "failed": 1, "stuck": 0, "records": 12,
                                   "last": None, "last_failure": None}],
              "failures": [{"job": "kovo.match", "status": "FAILED", "target": "023-9", "error": "HTTP 503",
                            "started": None}]}


class JobStatusTest(TestCase):
    """작업 현황 화면(실패 알림 목록 화면 위에 DAG·수집기 상태를 함께 보여 줌)."""

    @classmethod
    def setUpTestData(cls):
        """관리자 계정과, 웹훅이 설정되지 않아 보내지 못한(NO_WEBHOOK) 실패 알림 하나."""
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")
        JobAlert.objects.create(dag_id="kovo_postgame", task_id="curate", run_id="r1", try_number=4,
                                error="CalledProcessError: exit 137", log_url="http://localhost:8080/x",
                                delivery="NO_WEBHOOK")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_status_page_shows_jobs_collectors_and_failures(self):
        """DAG 이름, 수집 작업, 수집 실패 내용, 작업 실패 알림, 미확인 건수, 웹훅 설정 안내가 보인다."""
        with mock.patch("ops.jobs.overview", return_value=DAG_ROWS), \
             mock.patch("ops.jobs.collection_runs", return_value=COLLECTION):
            res = self.client.get(reverse("admin:ops_jobalert_changelist"))
        self.assertContains(res, "경기 후 기록")
        self.assertContains(res, "kovo.match")
        self.assertContains(res, "HTTP 503")
        self.assertContains(res, "CalledProcessError: exit 137")
        self.assertContains(res, "확인하지 않은 작업 실패가 1건")
        self.assertContains(res, "ALERT_WEBHOOK_URL")  # 마지막 알림을 어디로도 보내지 못했으므로 설정 안내

    def test_status_page_survives_airflow_and_elasticsearch_down(self):
        """Airflow나 Elasticsearch가 꺼져 있어도 화면은 뜨고, 읽지 못한 부분만 안내한다."""
        with mock.patch("ops.jobs.overview", side_effect=jobs.ServiceError("Airflow 꺼짐")), \
             mock.patch("ops.jobs.collection_runs", side_effect=ConnectionError("es down")):
            res = self.client.get(reverse("admin:ops_jobalert_changelist"))
        self.assertContains(res, "Airflow를 읽지 못했습니다: Airflow 꺼짐")
        self.assertContains(res, "수집 기록을 읽지 못했습니다")

    def test_admin_index_notice_and_acknowledge(self):
        """관리자 첫 화면에 미확인 실패 안내가 뜨고, "확인" 동작을 하면 확인자와 함께 상태가 바뀌어 안내가 사라진다."""
        self.assertContains(self.client.get(reverse("admin:index")), "확인하지 않은 작업 실패가 1건")
        alert = JobAlert.objects.get()
        with mock.patch("ops.jobs.overview", return_value=[]), mock.patch("ops.jobs.collection_runs", return_value=None):
            self.client.post(reverse("admin:ops_jobalert_changelist"),
                             {"action": "acknowledge", "_selected_action": [alert.pk]})
        alert.refresh_from_db()
        self.assertEqual((alert.status, alert.acknowledged_by), (JobAlert.Status.ACKNOWLEDGED, self.admin))
        self.assertNotContains(self.client.get(reverse("admin:index")), "확인하지 않은 작업 실패")

    def test_collection_runs_pairs_start_and_finish(self):
        """수집 실행 기록의 시작(RUNNING)과 끝(SUCCESS/FAILED)을 run_id로 짝지어 작업별 성공·실패·멈춤을 센다.
        끝 기록 없이 오래된 RUNNING은 멈춘(stuck) 것으로, 방금 시작한 것은 아직 진행 중으로 본다."""
        now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)

        def doc(rid, status, started, job="kovo.match", **kw):
            """Elasticsearch 검색 결과의 수집 기록 문서 하나."""
            return {"_source": {"collection": {"run_id": rid, "job_name": job, "status": status,
                                               "started_at": started, **kw}}}

        hits = [  # 실제 조회처럼 최신 기록부터
            doc("a", "SUCCESS", "2026-11-06T11:00:00+00:00", finished_at="2026-11-06T11:01:00+00:00", record_count=5),
            doc("a", "RUNNING", "2026-11-06T11:00:00+00:00"),
            doc("b", "FAILED", "2026-11-06T10:00:00+00:00", finished_at="2026-11-06T10:00:30+00:00",
                record_count=0, error_message="HTTP 503"),
            doc("b", "RUNNING", "2026-11-06T10:00:00+00:00"),
            doc("c", "RUNNING", "2026-11-06T05:00:00+00:00"),                       # 끝나지 않음: 멈춤
            doc("d", "RUNNING", "2026-11-06T11:50:00+00:00", job="news_collect"),   # 아직 진행 중: 멈춤 아님
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
        """Airflow에서 DAG 목록·최근 실행·7일간 실패 수를 읽어, 마지막 실행 상태와 걸린 시간(분)을 계산한다."""
        def fake(method, url, body=None, timeout=10):
            """Airflow API 흉내: DAG 목록, 실패 실행 수, 최근 실행 목록."""
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
        self.assertEqual([r["run_id"] for r in daily["dots"]], ["s1", "s2"])  # 오래된 실행부터
        self.assertTrue(all(r["missing"] for r in rows[1:]))                    # Airflow 응답에 없는 DAG


class ManualRunTest(TestCase):
    """수동 실행 화면: 입력 폼 검사, Airflow에 보내는 설정(conf), 실행 기록, 권한."""

    @classmethod
    def setUpTestData(cls):
        make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")

    def setUp(self):
        self.client.force_login(self.admin)

    def run_form(self, kind, **fields):
        """kind 폼을 제출한다(필드 이름 앞에 "kind-"가 붙음). Airflow 실행 요청(jobs.trigger)은 가짜로 바꿔 함께 돌려준다."""
        data = {"kind": kind, **{f"{kind}-{k}": v for k, v in fields.items()}}
        with mock.patch("ops.jobs.trigger", return_value="manual__1") as trigger:
            res = self.client.post(reverse("admin:ops_manualrun_run"), data)
        return res, trigger

    def test_reprocess_games_and_dates(self):
        """재처리 폼: 경기·기간·재수집 여부를 kovo_reprocess DAG 설정으로 보내고, 요청자와 메모를 기록한다."""
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
        """잘못된 입력(빈 재처리, 틀린 형식, 거꾸로 된 기간, 없는 경기, 배속 0, 바로 실행할 수 없는 DAG)은
        폼에 오류를 보여 주고 Airflow에는 보내지 않는다."""
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
        """지금 실행·경기 재생·과거 시즌 수집이 각 DAG에 보내는 설정."""
        _, t = self.run_form("now", dag="kovo_postgame")
        self.assertEqual(t.call_args.args[:2], ("kovo_postgame", {}))
        _, t = self.run_form("replay", match="022-3", speed="20")
        self.assertEqual(t.call_args.args[:2], ("live_replay", {"match": "022-3", "speed": 20.0}))
        _, t = self.run_form("backfill", seasons="019,020")
        self.assertEqual(t.call_args.args[:2], ("kovo_backfill", {"seasons": "019,020", "limit": None,
                                                                  "skip_confirmed": False}))

    def test_airflow_error_and_permission(self):
        """Airflow가 거절하면 안내만 하고 기록하지 않는다. 보기 권한만 있는 직원은 실행할 수 없다."""
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
        """수동 실행 화면에 네 가지 폼이 모두 보인다."""
        res = self.client.get(reverse("admin:ops_manualrun_changelist"))
        for text in ("예약 작업 지금 실행", "경기·날짜·시즌 재처리", "과거 시즌 수집", "경기 재생", 'name="reprocess-games"'):
            self.assertContains(res, text)
