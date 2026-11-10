"""경기·리그 API 테스트. 손으로 만든 작은 리그(남자 팀 2개, 끝난 경기 1개와 예정 경기 1개)로 돈다.

  docker compose exec -T django-api python manage.py test matches

make_league()는 다른 앱의 테스트(accounts, live, analytics 등)도 가져다 쓰는 공용 데이터 준비 함수다.
"""
from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from league.models import Competition, Player, Season, Team

from .models import Match, PlayerAttackStat, PlayerMatchStat, RallyEvent, SetScore, TeamMatchStat

KST = timezone.get_fixed_timezone(9 * 60)  # 한국 시각(UTC+9)


def make_league():
    """테스트용 리그를 만들고 (끝난 경기, 예정 경기)를 돌려준다.

    022 시즌 정규리그(001/201)에:
      - 022-3  한국전력(홈) 0:3 우리카드, 확정(CONFIRMED). 세트 20:25, 20:25, 23:25,
               아라우조 23득점(+ 공격 유형 기록), 출전하지 않은 벤치 선수 1명, 팀 기록, 랠리 이벤트 2개
      - 022-10 우리카드(홈) vs 한국전력, 이틀 뒤 예정
      - 022-4  여자부 예정 경기(성별 필터 확인용)
    """
    season = Season.objects.create(code="022", name="2025~2026 V-리그", years="2025-2026")
    regular = Competition.objects.create(season=season, gcode="001", league_code="201", type="REGULAR", name="정규리그")
    a = Team.objects.create(code="1006", name="한국전력", gender="M")
    b = Team.objects.create(code="1009", name="우리카드", gender="M")
    w = Team.objects.create(code="2001", name="여자팀", gender="F")
    w2 = Team.objects.create(code="2002", name="여자팀2", gender="F")
    finished = Match.objects.create(competition=regular, game_no=3, round=1, gender="M", home_team=a, away_team=b,
                                    scheduled_at=datetime(2025, 10, 20, 19, 0, tzinfo=KST), venue="수원체육관",
                                    status="CONFIRMED", home_sets=0, away_sets=3, duration_min=88, spectators=1348)
    for n, (h, aw) in enumerate([(20, 25), (20, 25), (23, 25)], 1):
        SetScore.objects.create(match=finished, set_no=n, home_points=h, away_points=aw)
    later = Match.objects.create(competition=regular, game_no=10, round=1, gender="M", home_team=b, away_team=a,
                                 scheduled_at=timezone.now() + timedelta(days=2), status="SCHEDULED")
    Match.objects.create(competition=regular, game_no=4, round=1, gender="F", home_team=w, away_team=w2,
                         scheduled_at=datetime(2025, 11, 1, 16, 0, tzinfo=KST), status="SCHEDULED")
    p = Player.objects.create(code="1000525", name="아라우조", gender="M", position="OP")
    bench = Player.objects.create(code="1000999", name="벤치", gender="M")
    pms = PlayerMatchStat.objects.create(match=finished, player=p, team=b, played_sets="11100", points=23,
                                         attack_attempts=29, attack_success=17, block_success=4, serve_aces=2,
                                         back_number=20, position="OP")
    PlayerAttackStat.objects.create(player_match_stat=pms, attack_type="OPEN", attempts=10, success=5)
    PlayerMatchStat.objects.create(match=finished, player=bench, team=b, played_sets="00000")
    for team, pts in ((a, 63), (b, 84)):
        TeamMatchStat.objects.create(match=finished, team=team, points=pts, attack_attempts=70, attack_success=35)
    RallyEvent.objects.create(match=finished, set_no=1, event_no=1, rally_no=0, seq=1, team=b, action_code="ST",
                              result_code="tot", home_score=0, away_score=0)
    RallyEvent.objects.create(match=finished, set_no=1, event_no=2, rally_no=1, seq=1, team=b, player=p,
                              action_code="AA", result_code="suc", grade=1, home_score=0, away_score=1)
    return finished, later


class MatchApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.finished, cls.later = make_league()

    def test_list_by_month_and_gender(self):
        """시즌·성별·월로 거르고, 점수와 세트별 점수, 한국 시각 예정 시각을 돌려준다."""
        r = self.client.get("/api/matches", {"season": "022", "gender": "M", "month": "2025-10"}).json()
        self.assertEqual([m["key"] for m in r["results"]], ["022-3"])
        m = r["results"][0]
        self.assertEqual((m["home"]["name"], m["away_sets"], m["sets"]), ("한국전력", 3, [[20, 25], [20, 25], [23, 25]]))
        self.assertEqual(m["scheduled_at"], "2025-10-20T19:00:00+09:00")

    def test_list_by_team_and_status(self):
        """팀(홈·원정 모두)과 상태(finished/scheduled)로 거른다. 결과는 날짜순."""
        keys = lambda **q: [m["key"] for m in self.client.get("/api/matches", q).json()["results"]]  # noqa: E731
        self.assertEqual(keys(season="022", team="1009"), ["022-3", "022-10"])
        self.assertEqual(keys(status="finished"), ["022-3"])
        self.assertEqual(keys(status="scheduled", gender="M"), ["022-10"])

    def test_bad_filters_are_400(self):
        """형식이 틀린 월이나 모르는 성별 코드는 400."""
        self.assertEqual(self.client.get("/api/matches", {"month": "2025/10"}).status_code, 400)
        self.assertEqual(self.client.get("/api/matches", {"gender": "X"}).status_code, 400)

    def test_detail_has_sets_box_scores_and_rally_sets(self):
        """경기 상세: 세트 점수, 팀 기록(공격 성공률 계산), 출전한 선수만의 박스스코어, 랠리 기록이 있는 세트, 출처."""
        r = self.client.get("/api/matches/022-3").json()
        self.assertEqual([s["home_points"] for s in r["set_details"]], [20, 20, 23])
        self.assertEqual(r["team_stats"]["away"]["points"], 84)
        self.assertEqual(r["team_stats"]["away"]["attack_pct"], 50.0)
        self.assertEqual([p["player"]["name"] for p in r["players"]["away"]], ["아라우조"])  # 출전 안 한 벤치 선수는 숨김
        self.assertEqual(r["players"]["away"][0]["sets_played"], 3)
        self.assertEqual(r["rally_sets"], [1])
        self.assertEqual(r["source"]["name"], "KOVO 한국배구연맹")

    def test_unknown_match_is_404(self):
        """없는 경기는 404."""
        self.assertEqual(self.client.get("/api/matches/022-999").status_code, 404)

    def test_rallies_of_one_set(self):
        """한 세트의 랠리 이벤트를 순서대로, 선수 이름과 함께 돌려준다."""
        r = self.client.get("/api/matches/022-3/rallies", {"set": 1}).json()
        self.assertEqual([(e["action"], e["away_score"]) for e in r["events"]], [("ST", 0), ("AA", 1)])
        self.assertEqual(r["events"][1]["player"]["name"], "아라우조")


class LeagueApiTest(TestCase):
    """시즌 목록·순위표·선수 순위·팀 상세·선수 상세 API."""

    @classmethod
    def setUpTestData(cls):
        make_league()

    def test_seasons(self):
        """시즌마다 그 시즌의 대회(리그 코드) 목록이 붙는다."""
        r = self.client.get("/api/seasons").json()
        self.assertEqual([(s["code"], [c["league_code"] for c in s["competitions"]]) for s in r], [("022", ["201"])])

    def test_standings_requires_gender_and_ranks_teams(self):
        """순위표는 성별이 필수이고, 승점·승수 순으로 줄 세우며 총 득점(points_won)도 준다."""
        self.assertEqual(self.client.get("/api/standings").status_code, 400)
        rows = self.client.get("/api/standings", {"gender": "M"}).json()["rows"]
        self.assertEqual([(r["team"]["name"], r["points"], r["wins"]) for r in rows], [("우리카드", 3, 1), ("한국전력", 0, 0)])
        self.assertEqual(rows[0]["points_won"], 75)

    def test_player_ranking(self):
        """선수 순위(득점 부문). 모르는 부문은 400."""
        r = self.client.get("/api/standings/players", {"gender": "M", "category": "points"}).json()
        self.assertEqual([(x["player"]["name"], x["value"]) for x in r["rows"]], [("아라우조", 23)])
        self.assertEqual(self.client.get("/api/standings/players", {"gender": "M", "category": "x"}).status_code, 400)

    def test_team_detail(self):
        """팀 상세: 순위·성적, 출전 선수 명단, 최근·다음 경기, 리그 평균과 비교할 기록."""
        r = self.client.get("/api/teams/1009").json()
        self.assertEqual((r["record"]["rank"], r["record"]["wins"]), (1, 1))
        self.assertEqual([x["player"]["name"] for x in r["roster"]], ["아라우조"])
        self.assertEqual([m["key"] for m in r["recent"]], ["022-3"])
        self.assertEqual([m["key"] for m in r["upcoming"]], ["022-10"])
        self.assertIsNotNone(r["stats"]["league"]["attack_pct"])

    def test_player_detail(self):
        """선수 상세: 현재 팀·등번호, 시즌별 기록, 경기별 기록(상대·결과·선수 팀 기준 스코어·홈 여부)."""
        r = self.client.get("/api/players/1000525").json()
        self.assertEqual((r["team"]["name"], r["back_number"]), ("우리카드", 20))
        self.assertEqual(r["career"][0]["points"], 23)
        log = r["matches"][0]
        self.assertEqual((log["opponent"]["name"], log["result"], log["score"], log["home"]), ("한국전력", "W", "3:0", False))


class PredictionApiTest(TestCase):
    """경기 API에 붙는 경기 전 예측, 상대 전적, 최근 흐름(WEB-08).

    예정 경기 022-10에 챔피언 모델(v1)의 예측(홈 승리 61%, 예상 3:1)을 넣어 둔다.
    """

    @classmethod
    def setUpTestData(cls):
        from analytics.models import MatchPrediction, ModelVersion

        cls.finished, cls.later = make_league()
        mv = ModelVersion.objects.create(name="match-winner", version=1, mlflow_run_id="r1", alias="champion",
                                         metrics={"test_accuracy": 0.64, "elo_test_accuracy": 0.62}, test_season="022")
        MatchPrediction.objects.create(match=cls.later, model_version=mv, home_win_prob="0.6100",
                                       set_score_probs={"3:0": 0.2, "3:1": 0.25, "3:2": 0.16, "2:3": 0.14, "1:3": 0.15, "0:3": 0.1},
                                       pred_home_sets=3, pred_away_sets=1, predicted_at=timezone.now())

    def test_list_carries_the_prediction(self):
        """경기 목록에 예측이 붙는다(예측이 없는 경기는 None)."""
        rows = {m["key"]: m for m in self.client.get("/api/matches", {"season": "022", "gender": "M"}).json()["results"]}
        self.assertEqual(rows["022-10"]["prediction"]["home_win_prob"], 0.61)
        self.assertIsNone(rows["022-3"]["prediction"])

    def test_preview_has_prediction_head_to_head_and_form(self):
        """경기 상세의 preview: 예측(예상 세트, 모델 정보), 두 팀의 상대 전적, 팀별 최근 경기 결과."""
        pv = self.client.get("/api/matches/022-10").json()["preview"]
        self.assertEqual(pv["prediction"]["pred_sets"], [3, 1])
        self.assertEqual(pv["prediction"]["model"]["test_season_years"], "2025-2026")
        # 022-10은 우리카드(홈) vs 한국전력. 앞선 맞대결 022-3은 원정 우리카드가 3:0으로 이겼다
        self.assertEqual((pv["head_to_head"]["total"], pv["head_to_head"]["wins"]["home"]), (1, 1))
        self.assertEqual(pv["head_to_head"]["recent"][0]["score"], "3:0")
        self.assertEqual([f["result"] for f in pv["home"]["form"]], ["W"])
        self.assertEqual([f["result"] for f in pv["away"]["form"]], ["L"])
