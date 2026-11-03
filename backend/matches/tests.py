"""API tests on a small hand-made league (two men's teams, one finished and one scheduled match)."""
from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from league.models import Competition, Player, Season, Team

from .models import Match, PlayerAttackStat, PlayerMatchStat, RallyEvent, SetScore, TeamMatchStat

KST = timezone.get_fixed_timezone(9 * 60)


def make_league():
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
        r = self.client.get("/api/matches", {"season": "022", "gender": "M", "month": "2025-10"}).json()
        self.assertEqual([m["key"] for m in r["results"]], ["022-3"])
        m = r["results"][0]
        self.assertEqual((m["home"]["name"], m["away_sets"], m["sets"]), ("한국전력", 3, [[20, 25], [20, 25], [23, 25]]))
        self.assertEqual(m["scheduled_at"], "2025-10-20T19:00:00+09:00")

    def test_list_by_team_and_status(self):
        keys = lambda **q: [m["key"] for m in self.client.get("/api/matches", q).json()["results"]]  # noqa: E731
        self.assertEqual(keys(season="022", team="1009"), ["022-3", "022-10"])
        self.assertEqual(keys(status="finished"), ["022-3"])
        self.assertEqual(keys(status="scheduled", gender="M"), ["022-10"])

    def test_bad_filters_are_400(self):
        self.assertEqual(self.client.get("/api/matches", {"month": "2025/10"}).status_code, 400)
        self.assertEqual(self.client.get("/api/matches", {"gender": "X"}).status_code, 400)

    def test_detail_has_sets_box_scores_and_rally_sets(self):
        r = self.client.get("/api/matches/022-3").json()
        self.assertEqual([s["home_points"] for s in r["set_details"]], [20, 20, 23])
        self.assertEqual(r["team_stats"]["away"]["points"], 84)
        self.assertEqual(r["team_stats"]["away"]["attack_pct"], 50.0)
        self.assertEqual([p["player"]["name"] for p in r["players"]["away"]], ["아라우조"])  # bench player hidden
        self.assertEqual(r["players"]["away"][0]["sets_played"], 3)
        self.assertEqual(r["rally_sets"], [1])
        self.assertEqual(r["source"]["name"], "KOVO 한국배구연맹")

    def test_unknown_match_is_404(self):
        self.assertEqual(self.client.get("/api/matches/022-999").status_code, 404)

    def test_rallies_of_one_set(self):
        r = self.client.get("/api/matches/022-3/rallies", {"set": 1}).json()
        self.assertEqual([(e["action"], e["away_score"]) for e in r["events"]], [("ST", 0), ("AA", 1)])
        self.assertEqual(r["events"][1]["player"]["name"], "아라우조")


class LeagueApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_league()

    def test_seasons(self):
        r = self.client.get("/api/seasons").json()
        self.assertEqual([(s["code"], [c["league_code"] for c in s["competitions"]]) for s in r], [("022", ["201"])])

    def test_standings_requires_gender_and_ranks_teams(self):
        self.assertEqual(self.client.get("/api/standings").status_code, 400)
        rows = self.client.get("/api/standings", {"gender": "M"}).json()["rows"]
        self.assertEqual([(r["team"]["name"], r["points"], r["wins"]) for r in rows], [("우리카드", 3, 1), ("한국전력", 0, 0)])
        self.assertEqual(rows[0]["points_won"], 75)

    def test_player_ranking(self):
        r = self.client.get("/api/standings/players", {"gender": "M", "category": "points"}).json()
        self.assertEqual([(x["player"]["name"], x["value"]) for x in r["rows"]], [("아라우조", 23)])
        self.assertEqual(self.client.get("/api/standings/players", {"gender": "M", "category": "x"}).status_code, 400)

    def test_team_detail(self):
        r = self.client.get("/api/teams/1009").json()
        self.assertEqual((r["record"]["rank"], r["record"]["wins"]), (1, 1))
        self.assertEqual([x["player"]["name"] for x in r["roster"]], ["아라우조"])
        self.assertEqual([m["key"] for m in r["recent"]], ["022-3"])
        self.assertEqual([m["key"] for m in r["upcoming"]], ["022-10"])
        self.assertIsNotNone(r["stats"]["league"]["attack_pct"])

    def test_player_detail(self):
        r = self.client.get("/api/players/1000525").json()
        self.assertEqual((r["team"]["name"], r["back_number"]), ("우리카드", 20))
        self.assertEqual(r["career"][0]["points"], 23)
        log = r["matches"][0]
        self.assertEqual((log["opponent"]["name"], log["result"], log["score"], log["home"]), ("한국전력", "W", "3:0", False))
