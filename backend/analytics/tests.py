"""분석 기능 테스트: 선수 최근 흐름, 예측 검증 화면, 팀 분석, 선수 능력치 카드, 예측 모델 관리(관리자).

  docker compose exec -T django-api python manage.py test analytics

데이터는 matches.tests.make_league()의 작은 리그 위에 필요한 분석 행(예측, 레이팅, 능력치 카드)을 직접 넣어 만든다.
MLflow·Airflow 호출은 mock으로 바꿔 끼워 실제 서비스 없이 돈다.
"""
from django.test import TestCase


class PlayerFormTests(TestCase):
    """선수 최근 흐름(analytics.form, ANL-08): 분명하고 의미 있는 변화만 상승/하락으로 부른다.

    최근 5경기를 그 전 경기들과 비교해, 차이가 우연한 흔들림보다 크고 실제로도 큰 차이일 때만 추세로 본다.
    """

    @staticmethod
    def game(points, sets=4, att=10, suc=5, rec=0, exc=0, err=0):
        """선수의 경기 기록 한 줄(세트, 득점, 공격 시도·성공, 리시브 시도·정확·실패)."""
        return {"sets": sets, "points": points, "attack_attempts": att, "attack_success": suc,
                "receive_attempts": rec, "receive_excellent": exc, "receive_errors": err}

    def test_too_few_games(self):
        """경기가 너무 적으면(7경기) 판단하지 않는다(None)."""
        from analytics.form import player_form

        self.assertIsNone(player_form([self.game(10)] * 7))

    def test_clear_drop_is_down_and_noise_is_flat(self):
        """세트당 득점이 20점대에서 8점대로 떨어지면 하락, 비슷한 범위 안에서 오르내리면 보합(flat)."""
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
        """통계적으로는 달라도 실제 크기가 아주 작은 변화(세트당 0 → 0.25점)는 추세가 아니다."""
        from analytics.form import player_form

        f = player_form([self.game(0, att=0, suc=0)] * 8 + [self.game(1, att=0, suc=0)] * 5)
        self.assertEqual(f["metrics"][0]["trend"], "flat")  # 세트당 0 → 0.25점


class PredictionCheckTests(TestCase):
    """예측 검증 API GET /api/predictions(ANL-10): 시즌별 백테스트(Elo와 비교), 확률 구간별 보정, 실제 경기 결과."""

    def test_backtest_and_live_results(self):
        """백테스트 적중률을 모델과 Elo로 나눠 내고, 실전 예측의 적중 수·최근 결과를 성별로 거를 수 있다."""
        from datetime import datetime, timezone as tz

        from analytics.models import MatchPrediction, ModelVersion, PredictionBacktest
        from matches.models import Match
        from matches.tests import make_league

        make_league()
        finished = Match.objects.get(game_no=3)  # 홈이 0:3으로 짐
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
        self.assertEqual(season["model"]["accuracy"], 1.0)   # 홈 30% → 원정 승 예측, 맞음
        self.assertEqual(season["elo"]["accuracy"], 0.0)     # Elo는 홈 60%라고 했다(틀림)
        self.assertEqual(r["backtest"]["calibration"][0]["from"], 0.7)
        self.assertEqual(r["live"]["overall"]["n"], 1)
        self.assertEqual(r["live"]["recent"][0]["sets"], [0, 3])
        self.assertEqual(self.client.get("/api/predictions?gender=F").json()["live"]["overall"]["n"], 0)


class TeamAnalysisTests(TestCase):
    """팀 상세 API의 분석 부분: 팀 능력 프로필(리그 안 상대 위치)과 Elo 레이팅 추이."""

    def test_team_profile_ranks_among_teams(self):
        """프로필 축마다 몇 팀 중 어디쯤인지(0~100) 준다. 경기가 하나뿐이면 최근 흐름은 아직 없다."""
        from matches.tests import make_league

        make_league()
        r = self.client.get("/api/teams/1009?season=022").json()
        attack = next(a for a in r["profile"]["axes"] if a["key"] == "attack_pct")
        self.assertEqual(attack["teams"], 2)
        self.assertIn(attack["score"], (0, 50, 100))
        self.assertEqual(r["form"], [])  # 한 경기뿐: 아직 흐름 없음

    def test_early_season_rating_includes_the_end_of_last_season(self):
        """시즌 초에는 레이팅 기록이 짧으므로 지난 시즌 마지막 10경기의 레이팅도 함께 준다(이어서 보이도록)."""
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
        self.assertEqual([p["rating"] for p in tail], [1500.0 + n for n in range(2, 12)])  # 마지막 10개, 오래된 것부터


class PlayerRatingApiTests(TestCase):
    """포지션별 능력치(선수 카드): 능력치 순위표(시즌/통산)와 선수 화면의 카드.

    아포짓(OP) 남자 선수 셋: 아라우조(022 시즌 81점, 021 시즌 74점), 두번째(66점), 신인(90점이지만 규정 미달).
    통산 카드: 아라우조 78, 두번째 80, 신인 95(규정 시즌 부족으로 순위 제외).
    """

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
            """시즌 능력치 카드 한 장(능력치는 득점력 하나만)."""
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
            """통산 능력치 카드 한 장. qualified=False면 규정 시즌이 1개뿐이라 통산 순위에 들지 못한다."""
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
        """시즌 순위표는 규정을 채운 선수만 종합 점수순으로 매긴다. all=1이면 미달 선수도 순위 없이 보여 준다."""
        r = self.client.get("/api/ratings", {"season": "022", "gender": "M", "position": "OP"}).json()
        assert r["position_name"] == "아포짓"
        assert [(x["rank"], x["player"]["name"], x["overall"]) for x in r["rows"]] == [(1, "아라우조", 81), (2, "두번째", 66)]
        assert r["abilities"] == [{"key": "scoring", "label": "득점력", "weight": 25}]
        with_all = self.client.get("/api/ratings", {"season": "022", "gender": "M", "position": "OP", "all": "1"}).json()
        assert with_all["rows"][0]["player"]["name"] == "신인" and with_all["rows"][0]["rank"] is None  # 보이지만 순위는 없음
        assert self.client.get("/api/ratings", {"gender": "M", "position": "XX"}).status_code == 400
        assert self.client.get("/api/ratings", {"position": "OP"}).status_code == 400  # 성별은 필수

    def test_career_board_ranks_careers_with_three_qualified_seasons(self):
        """통산 순위표(scope=career)는 규정 시즌 3개 이상인 선수만 매기고, 현재 팀·첫 시즌을 함께 준다."""
        r = self.client.get("/api/ratings", {"scope": "career", "gender": "M", "position": "OP"}).json()
        assert r["scope"] == "career" and r["season"] is None
        assert [(x["rank"], x["player"]["name"], x["overall"]) for x in r["rows"]] == [(1, "두번째", 80), (2, "아라우조", 78)]
        assert r["rows"][1]["team"]["code"] == "1009" and r["rows"][1]["first"]["code"] == "021"
        with_all = self.client.get("/api/ratings", {"scope": "career", "gender": "M", "position": "OP", "all": "1"}).json()
        assert with_all["rows"][0]["player"]["name"] == "신인" and with_all["rows"][0]["rank"] is None

    def test_player_page_carries_every_season_and_the_career_card(self):
        """선수 화면: 시즌별 카드(오래된 것부터, 지난 시즌 대비 변화 포함)와 통산 카드. 카드가 없는 선수는 None."""
        ratings = self.client.get("/api/players/1000525", {"season": "022"}).json()["ratings"]
        old, now = ratings["seasons"]  # 오래된 시즌부터
        assert (old["season"]["code"], old["previous"], old["rank"]) == ("021", None, 1)
        assert (now["overall"], now["rank"], now["peers"], now["team"]["code"]) == (81, 1, 2, "1009")
        assert now["previous"] == {"season": {"code": "021", "years": "2024-2025"}, "overall": 74,
                                   "abilities": {"scoring": 70}}
        assert now["abilities"][0]["stats"][0]["label"] == "세트당 득점" and "z" not in now["abilities"][0]
        [career] = ratings["careers"]
        assert (career["overall"], career["rank"], career["peers"]) == (78, 2, 2)  # 95점 통산 카드(신인)는 순위에 없다
        assert career["first"] == {"code": "021", "years": "2024-2025"} and career["best"]["overall"] == 81
        assert career["abilities"][0]["stats"][0]["period"] == "2023–24부터"
        assert self.client.get("/api/players/1000999", {"season": "022"}).json()["ratings"] is None


class ModelManagementTests(TestCase):
    """예측 모델 관리 관리자 화면(ADM-05): MLflow 버전 목록을 보여 주고 서비스 모델(champion)을 바꾼다.

    MLflow·Airflow REST 호출(analytics.registry._call)은 가짜 함수로 바꿔, 보낸 요청을 self.calls에 기록한다.
    등록된 버전: v5, v6(서비스 중 champion), v7(후보 candidate).
    """

    REGISTERED = {"registered_model": {"aliases": [{"alias": "champion", "version": "6"},
                                                   {"alias": "candidate", "version": "7"}]}}
    VERSIONS = {"model_versions": [
        {"version": str(n), "status": "READY", "creation_timestamp": 1793850342644, "run_id": f"run{n}",
         "tags": [{"key": "test_accuracy", "value": acc}, {"key": "test_log_loss", "value": ll},
                  {"key": "elo_test_log_loss", "value": "0.662240"}, {"key": "test_season", "value": "022"},
                  {"key": "train_seasons", "value": "001-022"}]}
        for n, acc, ll in ((5, "0.6240", "0.652600"), (6, "0.6353", "0.649811"), (7, "0.6100", "0.670000"))]}

    def setUp(self):
        """가짜 MLflow·Airflow를 끼우고, 관리자 계정으로 로그인한다(테스트 DB에만 만들어지는 계정)."""
        from unittest import mock

        from django.contrib.auth import get_user_model

        self.calls = []

        def fake(method, url, body=None, timeout=10):
            """요청을 기록하고, 주소에 따라 모델 정보·버전 목록·DAG 실행 결과를 흉내 낸다."""
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
        """버전마다 상태(서비스 중/후보)와 지표를 보여 주고, Elo보다 나은 값은 강조하며, 현재 버전은 고를 수 없다."""
        r = self.client.get("/admin/analytics/modelchange/")
        self.assertContains(r, "서비스 중")
        self.assertContains(r, "후보")
        self.assertContains(r, "63.5%")                              # v6 시험 적중률
        self.assertContains(r, '<span class="better">0.6498</span>')  # Elo 기준선보다 나음
        self.assertContains(r, 'value="6" id="v6"\n                     disabled')  # 지금 서비스 중인 버전은 고를 수 없음

    def test_switch_records_moves_alias_and_reruns_predictions(self):
        """교체하면 이력(누가, 몇 → 몇, 이유)을 남기고, MLflow 별칭을 옮기고, 예측 DAG를 다시 돌린다."""
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
        """이유가 너무 짧거나, 이미 서비스 중인 버전이거나, 없는 버전이면 아무것도 바꾸지 않는다."""
        from analytics.models import ModelChange

        self.client.post("/admin/analytics/modelchange/promote/", {"version": "5", "reason": "짧음"})
        self.client.post("/admin/analytics/modelchange/promote/", {"version": "6", "reason": "이미 서비스 중인 버전"})
        self.client.post("/admin/analytics/modelchange/promote/", {"version": "9", "reason": "없는 버전으로 교체"})
        self.assertFalse(ModelChange.objects.exists())
        self.assertFalse([c for c in self.calls if c[0] == "POST"])

    def test_registry_down_is_reported_not_an_error_page(self):
        """MLflow에 연결할 수 없으면 오류 페이지 대신 화면에 안내 문구를 보여 준다."""
        from unittest import mock

        from analytics import registry

        with mock.patch("analytics.registry._call", side_effect=registry.RegistryError("연결 실패")):
            r = self.client.get("/admin/analytics/modelchange/")
        self.assertContains(r, "MLflow를 읽지 못했습니다: 연결 실패")
