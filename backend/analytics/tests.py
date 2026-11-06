from django.test import TestCase

# Create your tests here.


class PlayerFormTests(TestCase):
    """analytics.form (ANL-08): only clear and meaningful changes are called a trend."""

    @staticmethod
    def game(points, sets=4, att=10, suc=5, rec=0, exc=0, err=0):
        return {"sets": sets, "points": points, "attack_attempts": att, "attack_success": suc,
                "receive_attempts": rec, "receive_excellent": exc, "receive_errors": err}

    def test_too_few_games(self):
        from analytics.form import player_form

        self.assertIsNone(player_form([self.game(10)] * 7))

    def test_clear_drop_is_down_and_noise_is_flat(self):
        from analytics.form import player_form

        before = [self.game(p) for p in (20, 22, 18, 21, 19, 20, 23, 17)]
        recent = [self.game(p, suc=3) for p in (8, 9, 7, 10, 8)]
        f = player_form(before + recent)
        pts = next(m for m in f["metrics"] if m["key"] == "points_per_set")
        self.assertEqual(pts["trend"], "down")
        self.assertEqual(f["overall"], "down")
        noisy = player_form(before + [self.game(p) for p in (18, 23, 19, 22, 20)])
        self.assertEqual(noisy["overall"], "flat")

    def test_tiny_changes_are_not_trends(self):
        from analytics.form import player_form

        f = player_form([self.game(0, att=0, suc=0)] * 8 + [self.game(1, att=0, suc=0)] * 5)
        self.assertEqual(f["metrics"][0]["trend"], "flat")  # 0 -> 0.25 points per set


class PredictionCheckTests(TestCase):
    """GET /api/predictions (ANL-10): backtest by season against Elo, calibration bins, real results."""

    def test_backtest_and_live_results(self):
        from datetime import datetime, timezone as tz

        from analytics.models import MatchPrediction, ModelVersion, PredictionBacktest
        from matches.models import Match
        from matches.tests import make_league

        make_league()
        finished = Match.objects.get(game_no=3)  # home lost 0:3
        mv = ModelVersion.objects.create(name="match-winner", version=6, mlflow_run_id="r", alias="champion",
                                         train_seasons="001-022", test_season="022")
        PredictionBacktest.objects.create(match=finished, model_version=mv, home_win_prob="0.3000",
                                          elo_home_win_prob="0.6000", pred_home_sets=1, pred_away_sets=3,
                                          home_won=False, train_seasons="001-021")
        MatchPrediction.objects.create(match=finished, model_version=mv, home_win_prob="0.4500", set_score_probs={},
                                       pred_home_sets=2, pred_away_sets=3, predicted_at=datetime(2025, 10, 19, tzinfo=tz.utc),
                                       is_correct=True)
        r = self.client.get("/api/predictions").json()
        season = r["backtest"]["seasons"][0]
        self.assertEqual(season["model"]["accuracy"], 1.0)   # 30% home -> away win predicted, right
        self.assertEqual(season["elo"]["accuracy"], 0.0)     # Elo said home 60%
        self.assertEqual(r["backtest"]["calibration"][0]["from"], 0.7)
        self.assertEqual(r["live"]["overall"]["n"], 1)
        self.assertEqual(r["live"]["recent"][0]["sets"], [0, 3])
        self.assertEqual(self.client.get("/api/predictions?gender=F").json()["live"]["overall"]["n"], 0)


class TeamAnalysisTests(TestCase):
    def test_team_profile_ranks_among_teams(self):
        from matches.tests import make_league

        make_league()
        r = self.client.get("/api/teams/1009?season=022").json()
        attack = next(a for a in r["profile"]["axes"] if a["key"] == "attack_pct")
        self.assertEqual(attack["teams"], 2)
        self.assertIn(attack["score"], (0, 50, 100))
        self.assertEqual(r["form"], [])  # one game: no form yet


class ModelManagementTests(TestCase):
    """ADM-05: the admin page lists MLflow versions and switches the champion; MLflow and Airflow are faked."""

    REGISTERED = {"registered_model": {"aliases": [{"alias": "champion", "version": "6"},
                                                   {"alias": "candidate", "version": "7"}]}}
    VERSIONS = {"model_versions": [
        {"version": str(n), "status": "READY", "creation_timestamp": 1793850342644, "run_id": f"run{n}",
         "tags": [{"key": "test_accuracy", "value": acc}, {"key": "test_log_loss", "value": ll},
                  {"key": "elo_test_log_loss", "value": "0.662240"}, {"key": "test_season", "value": "022"},
                  {"key": "train_seasons", "value": "001-022"}]}
        for n, acc, ll in ((5, "0.6240", "0.652600"), (6, "0.6353", "0.649811"), (7, "0.6100", "0.670000"))]}

    def setUp(self):
        from unittest import mock

        from django.contrib.auth import get_user_model

        self.calls = []

        def fake(method, url, body=None, timeout=10):
            self.calls.append((method, url.split("/api/")[1].split("?")[0], body))
            if "registered-models/get" in url:
                return self.REGISTERED
            if "model-versions/search" in url:
                return self.VERSIONS
            if "dagRuns" in url:
                return {"dag_run_id": "manual__1"}
            return {}

        patcher = mock.patch("analytics.registry._call", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")
        self.client.force_login(self.admin)

    def test_page_lists_versions_with_metrics(self):
        r = self.client.get("/admin/analytics/modelchange/")
        self.assertContains(r, "서비스 중")
        self.assertContains(r, "후보")
        self.assertContains(r, "63.5%")                              # v6 test accuracy
        self.assertContains(r, '<span class="better">0.6498</span>')  # beats the Elo baseline
        self.assertContains(r, 'value="6" id="v6"\n                     disabled')  # current champion not selectable

    def test_switch_records_moves_alias_and_reruns_predictions(self):
        from analytics.models import ModelChange

        r = self.client.post("/admin/analytics/modelchange/promote/", {"version": "5", "reason": "v6 실전 적중률이 낮음"})
        self.assertEqual(r.status_code, 302)
        change = ModelChange.objects.get()
        self.assertEqual((change.from_version, change.to_version, change.changed_by, change.predict_run),
                         (6, 5, self.admin, "manual__1"))
        self.assertIn(("POST", "2.0/mlflow/registered-models/alias",
                       {"name": "match-winner", "alias": "champion", "version": "5"}), self.calls)
        self.assertEqual(self.calls[-1][1], "v2/dags/ml_daily_predict/dagRuns")

    def test_switch_needs_a_reason_and_a_different_version(self):
        from analytics.models import ModelChange

        self.client.post("/admin/analytics/modelchange/promote/", {"version": "5", "reason": "짧음"})
        self.client.post("/admin/analytics/modelchange/promote/", {"version": "6", "reason": "이미 서비스 중인 버전"})
        self.client.post("/admin/analytics/modelchange/promote/", {"version": "9", "reason": "없는 버전으로 교체"})
        self.assertFalse(ModelChange.objects.exists())
        self.assertFalse([c for c in self.calls if c[0] == "POST"])

    def test_registry_down_is_reported_not_an_error_page(self):
        from unittest import mock

        from analytics import registry

        with mock.patch("analytics.registry._call", side_effect=registry.RegistryError("연결 실패")):
            r = self.client.get("/admin/analytics/modelchange/")
        self.assertContains(r, "MLflow를 읽지 못했습니다: 연결 실패")
