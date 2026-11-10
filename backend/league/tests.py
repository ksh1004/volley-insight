"""순위표·선수 순위 계산(league/standings.py) 테스트. DB 없이 순수 함수만 부른다(SimpleTestCase).

V-리그 승점 규칙: 3:0·3:1 승리 3점, 3:2 승리 2점, 2:3 패배 1점, 1:3·0:3 패배 0점.
순위: 승점 → 승수 → 세트 득실률 → 점수 득실률 순으로 비교한다.
"""
from django.test import SimpleTestCase

from .standings import match_points, player_ranking, team_standings


def m(home, away, hs, as_, hp=75, ap=70):
    """경기 한 건: (홈, 원정, 홈 세트, 원정 세트, 홈 총점, 원정 총점)."""
    return {"home": home, "away": away, "home_sets": hs, "away_sets": as_, "home_points": hp, "away_points": ap}


class MatchPointsTest(SimpleTestCase):
    def test_v_league_point_rule(self):
        """세트 스코어별 승점."""
        self.assertEqual([match_points(3, 0), match_points(3, 1), match_points(3, 2)], [3, 3, 2])
        self.assertEqual([match_points(2, 3), match_points(1, 3), match_points(0, 3)], [1, 0, 0])


class TeamStandingsTest(SimpleTestCase):
    TEAMS = [("A", "에이"), ("B", "비"), ("C", "씨")]

    def test_points_then_wins(self):
        """승점 순으로 줄 세우고, 최근 경기 결과(form)를 남긴다."""
        rows = team_standings(self.TEAMS, [m("A", "B", 3, 2), m("B", "C", 3, 0), m("C", "A", 3, 1)])
        # A: 2(3:2 승) + 0 = 2, B: 1 + 3 = 4, C: 0 + 3 = 3
        self.assertEqual([(r["team"]["code"], r["points"]) for r in rows], [("B", 4), ("C", 3), ("A", 2)])
        self.assertEqual(rows[0]["form"], ["L", "W"])

    def test_set_ratio_breaks_equal_points_and_wins(self):
        """승점과 승수가 같으면 세트 득실률(딴 세트 ÷ 내준 세트)이 높은 팀이 앞선다."""
        rows = team_standings(self.TEAMS[:2] + [("D", "디")],
                              [m("A", "C", 3, 0), m("B", "D", 3, 1), m("C", "B", 3, 0), m("D", "A", 3, 0)])
        a, b = (next(r for r in rows if r["team"]["code"] == c) for c in "AB")
        self.assertEqual((a["points"], b["points"], a["wins"], b["wins"]), (3, 3, 1, 1))
        self.assertLess(a["rank"], b["rank"])  # 승점·승수 같음. 세트 A 3:3(1.0) > B 3:4(0.75)
        self.assertEqual((a["set_ratio"], b["set_ratio"]), (1.0, 0.75))

    def test_fully_tied_teams_share_rank_and_unbeaten_ratio_is_none(self):
        """모든 기준이 같으면 공동 순위. 세트를 한 번도 안 내줬으면 득실률은 나눌 수 없어 None."""
        rows = team_standings([("A", "에이"), ("B", "비")], [])
        self.assertEqual([r["rank"] for r in rows], [1, 1])
        rows = team_standings(self.TEAMS[:2], [m("A", "B", 3, 0)])
        self.assertIsNone(rows[0]["set_ratio"])


def player(code, team, sets, **kw):
    """선수 시즌 합계 한 줄. 지정하지 않은 기록은 0."""
    base = {"code": code, "name": code, "team_code": team, "team_name": team, "games": 10, "sets_played": sets,
            "points": 0, "attack_success": 0, "attack_attempts": 0, "serve_aces": 0, "block_success": 0,
            "dig_success": 0, "set_success": 0, "receive_excellent": 0, "receive_errors": 0, "receive_attempts": 0}
    return {**base, **kw}


class PlayerRankingTest(SimpleTestCase):
    def test_players_below_a_third_of_team_sets_are_not_ranked_except_points(self):
        """팀 세트 수의 1/3 미만 출전 선수는 순위에서 빠진다. 단, 득점(누적 기록) 순위에는 들어간다."""
        players = [player("p1", "T", 30, points=100, block_success=30), player("p2", "T", 5, points=40, block_success=10)]
        self.assertEqual([r["player"]["code"] for r in player_ranking(players, {"T": 30}, "block")["rows"]], ["p1"])
        self.assertEqual([r["player"]["code"] for r in player_ranking(players, {"T": 30}, "points")["rows"]], ["p1", "p2"])

    def test_attack_rate_needs_enough_attempts_and_reports_them(self):
        """공격 성공률 순위는 시도가 충분한 선수만, 시도 수도 함께 보여 준다."""
        players = [player("main", "T", 30, attack_success=250, attack_attempts=500),
                   player("few", "T", 30, attack_success=30, attack_attempts=40)]
        rows = player_ranking(players, {"T": 30}, "attack")["rows"]
        self.assertEqual([(r["player"]["code"], r["value"], r["attack_attempts"]) for r in rows], [("main", 50.0, 500)])
