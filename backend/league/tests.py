from django.test import SimpleTestCase

from .standings import match_points, player_ranking, team_standings


def m(home, away, hs, as_, hp=75, ap=70):
    return {"home": home, "away": away, "home_sets": hs, "away_sets": as_, "home_points": hp, "away_points": ap}


class MatchPointsTest(SimpleTestCase):
    def test_v_league_point_rule(self):
        self.assertEqual([match_points(3, 0), match_points(3, 1), match_points(3, 2)], [3, 3, 2])
        self.assertEqual([match_points(2, 3), match_points(1, 3), match_points(0, 3)], [1, 0, 0])


class TeamStandingsTest(SimpleTestCase):
    TEAMS = [("A", "에이"), ("B", "비"), ("C", "씨")]

    def test_points_then_wins(self):
        rows = team_standings(self.TEAMS, [m("A", "B", 3, 2), m("B", "C", 3, 0), m("C", "A", 3, 1)])
        # A: 2 (3:2 win) + 0 = 2, B: 1 + 3 = 4, C: 0 + 3 = 3
        self.assertEqual([(r["team"]["code"], r["points"]) for r in rows], [("B", 4), ("C", 3), ("A", 2)])
        self.assertEqual(rows[0]["form"], ["L", "W"])

    def test_set_ratio_breaks_equal_points_and_wins(self):
        rows = team_standings(self.TEAMS[:2] + [("D", "디")],
                              [m("A", "C", 3, 0), m("B", "D", 3, 1), m("C", "B", 3, 0), m("D", "A", 3, 0)])
        a, b = (next(r for r in rows if r["team"]["code"] == c) for c in "AB")
        self.assertEqual((a["points"], b["points"], a["wins"], b["wins"]), (3, 3, 1, 1))
        self.assertLess(a["rank"], b["rank"])  # same points and wins; sets A 3:3 (1.0) > B 3:4 (0.75)
        self.assertEqual((a["set_ratio"], b["set_ratio"]), (1.0, 0.75))

    def test_fully_tied_teams_share_rank_and_unbeaten_ratio_is_none(self):
        rows = team_standings([("A", "에이"), ("B", "비")], [])
        self.assertEqual([r["rank"] for r in rows], [1, 1])
        rows = team_standings(self.TEAMS[:2], [m("A", "B", 3, 0)])
        self.assertIsNone(rows[0]["set_ratio"])


def player(code, team, sets, **kw):
    base = {"code": code, "name": code, "team_code": team, "team_name": team, "games": 10, "sets_played": sets,
            "points": 0, "attack_success": 0, "attack_attempts": 0, "serve_aces": 0, "block_success": 0,
            "dig_success": 0, "set_success": 0, "receive_excellent": 0, "receive_errors": 0, "receive_attempts": 0}
    return {**base, **kw}


class PlayerRankingTest(SimpleTestCase):
    def test_players_below_a_third_of_team_sets_are_not_ranked_except_points(self):
        players = [player("p1", "T", 30, points=100, block_success=30), player("p2", "T", 5, points=40, block_success=10)]
        self.assertEqual([r["player"]["code"] for r in player_ranking(players, {"T": 30}, "block")["rows"]], ["p1"])
        self.assertEqual([r["player"]["code"] for r in player_ranking(players, {"T": 30}, "points")["rows"]], ["p1", "p2"])

    def test_attack_rate_needs_enough_attempts_and_reports_them(self):
        players = [player("main", "T", 30, attack_success=250, attack_attempts=500),
                   player("few", "T", 30, attack_success=30, attack_attempts=40)]
        rows = player_ranking(players, {"T": 30}, "attack")["rows"]
        self.assertEqual([(r["player"]["code"], r["value"], r["attack_attempts"]) for r in rows], [("main", 50.0, 500)])
