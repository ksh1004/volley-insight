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

    def test_early_season_rating_includes_the_end_of_last_season(self):
        from datetime import datetime, timezone as tz

        from matches.models import Match
        from matches.tests import make_league

        from league.models import Competition, Season, Team

        from .models import TeamRating

        finished, _ = make_league()
        team = Team.objects.get(code="1009")
        old = Season.objects.create(code="021", name="2024~2025 V-리그", years="2024-2025")
        comp = Competition.objects.create(season=old, gcode="001", league_code="201", type="REGULAR", name="정규리그")
        for n in range(12):
            m = Match.objects.create(competition=comp, game_no=n + 1, round=1, gender="M", home_team=team,
                                     away_team=finished.home_team, scheduled_at=datetime(2025, 1, 1 + n, tzinfo=tz.utc),
                                     status="CONFIRMED", home_sets=3, away_sets=1)
            TeamRating.objects.create(team=team, season=old, match=m, rated_on=m.scheduled_at.date(),
                                      rating=1500 + n, rating_change=1, win_prob="0.5")
        TeamRating.objects.create(team=team, season=finished.competition.season, match=finished,
                                  rated_on=finished.scheduled_at.date(), rating=1510, rating_change=3, win_prob="0.5")
        rating = self.client.get("/api/teams/1009?season=022").json()["rating"]
        self.assertEqual(len(rating["history"]), 1)
        self.assertEqual(rating["previous"]["season"], {"code": "021", "years": "2024-2025"})
        tail = rating["previous"]["history"]
        self.assertEqual([p["rating"] for p in tail], [1500.0 + n for n in range(2, 12)])  # last 10, oldest first


class PlayerRatingApiTests(TestCase):
    """Position ratings (player cards): the leaderboard and the card on the player page."""

    @classmethod
    def setUpTestData(cls):
        from league.models import Player, Season, Team
        from matches.tests import make_league

        from .models import PlayerCareerRating, PlayerRating

        make_league()
        season = Season.objects.get(code="022")
        old = Season.objects.create(code="021", name="2024~2025 V-리그", years="2024-2025")
        team = Team.objects.get(code="1009")

        def card(player, s, overall, qualified=True, receive=70):
            return PlayerRating.objects.create(
                player=player, season=s, team=team, gender="M", position="OP", sets=90, qualified=qualified,
                provisional=not qualified, overall=overall,
                abilities=[{"key": "scoring", "label": "득점력", "weight": 25, "score": receive, "z": 0.8,
                            "stats": [{"metric": "points_ps", "label": "세트당 득점", "unit": "n2", "value": 4.1}],
                            "note": ""}],
                extra={})
        cls.star = Player.objects.get(code="1000525")
        cls.other = Player.objects.create(code="1000777", name="두번째", gender="M", position="OP")
        cls.rookie = Player.objects.create(code="1000888", name="신인", gender="M", position="OP")
        card(cls.star, season, 81, receive=84)
        card(cls.star, old, 74, receive=70)
        card(cls.other, season, 66)
        card(cls.rookie, season, 90, qualified=False)

        def career(player, overall, qualified=True):
            return PlayerCareerRating.objects.create(
                player=player, gender="M", position="OP", seasons=2, qualified_seasons=3 if qualified else 1, sets=180,
                first_season=old, last_season=season, best_season=season, best_overall=81, qualified=qualified,
                overall=overall, abilities=[{"key": "scoring", "label": "득점력", "weight": 25, "score": overall,
                                             "z": None, "note": "",
                                             "stats": [{"metric": "serve_pressure", "label": "서브 압박", "unit": "pct",
                                                        "value": 0.41, "period": "2023–24부터"}]}], extra={})
        career(cls.star, 78)
        career(cls.other, 80)
        career(cls.rookie, 95, qualified=False)

    def test_board_ranks_qualified_players_by_overall(self):
        r = self.client.get("/api/ratings", {"season": "022", "gender": "M", "position": "OP"}).json()
        assert r["position_name"] == "아포짓"
        assert [(x["rank"], x["player"]["name"], x["overall"]) for x in r["rows"]] == [(1, "아라우조", 81), (2, "두번째", 66)]
        assert r["abilities"] == [{"key": "scoring", "label": "득점력", "weight": 25}]
        with_all = self.client.get("/api/ratings", {"season": "022", "gender": "M", "position": "OP", "all": "1"}).json()
        assert with_all["rows"][0]["player"]["name"] == "신인" and with_all["rows"][0]["rank"] is None  # shown, not ranked
        assert self.client.get("/api/ratings", {"gender": "M", "position": "XX"}).status_code == 400
        assert self.client.get("/api/ratings", {"position": "OP"}).status_code == 400  # gender is required

    def test_player_page_carries_every_season_and_the_career_card(self):
        ratings = self.client.get("/api/players/1000525", {"season": "022"}).json()["ratings"]
        old, now = ratings["seasons"]  # oldest first
        assert (old["season"]["code"], old["previous"], old["rank"]) == ("021", None, 1)
        assert (now["overall"], now["rank"], now["peers"], now["team"]["code"]) == (81, 1, 2, "1009")
        assert now["previous"] == {"season": {"code": "021", "years": "2024-2025"}, "overall": 74,
                                   "abilities": {"scoring": 70}}
        assert now["abilities"][0]["stats"][0]["label"] == "세트당 득점" and "z" not in now["abilities"][0]
        [career] = ratings["careers"]
        assert (career["overall"], career["rank"], career["peers"]) == (78, 2, 2)  # the 95 career is not ranked
        assert career["first"] == {"code": "021", "years": "2024-2025"} and career["best"]["overall"] == 81
        assert career["abilities"][0]["stats"][0]["period"] == "2023–24부터"
        assert self.client.get("/api/players/1000999", {"season": "022"}).json()["ratings"] is None


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
