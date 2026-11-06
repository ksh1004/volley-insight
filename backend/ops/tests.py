"""Data quality issues (PIPE-10) and corrections (ADM-03): validation, apply/revert through the admin, match notes."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from league.models import Player
from matches.models import Match, SetScore, TeamMatchStat
from matches.tests import make_league

from .models import Correction, QualityIssue


def correction(**kw):
    return Correction(**{"target": "match", "key": "022-3", "field": "spectators", "value": "2000",
                         "reason": "KOVO 기록실 확인", **kw})


class CorrectionValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_league()

    def errors(self, **kw):
        try:
            correction(**kw).full_clean()
        except ValidationError as e:
            return e.message_dict
        return {}

    def test_valid_corrections(self):
        self.assertEqual(self.errors(), {})
        self.assertEqual(self.errors(target="set_score", key="022-3/2", field="home_points", value="25"), {})
        self.assertEqual(self.errors(target="player", key="1000525", field="position", value="OH"), {})
        self.assertEqual(self.errors(target="player", key="1000525", field="height_cm", value=""), {})  # blank = NULL

    def test_unknown_field_key_or_bad_value(self):
        self.assertIn("field", self.errors(field="status"))                       # computed by the pipeline
        self.assertIn("key", self.errors(key="022-999"))
        self.assertIn("key", self.errors(target="set_score", key="022-3", field="home_points"))  # no set number
        self.assertIn("key", self.errors(target="player_match_stat", key="022-3/0000001"))
        self.assertIn("value", self.errors(value="많음"))
        self.assertIn("value", self.errors(value="-5"))                          # positive field
        self.assertIn("value", self.errors(target="player", key="1000525", field="position", value="XX"))
        self.assertIn("value", self.errors(target="set_score", key="022-3/1", field="home_points", value=""))

    def test_one_active_correction_per_field(self):
        correction().save()
        self.assertIn("__all__", self.errors(value="3000"))
        self.assertEqual(self.errors(value="3000", is_active=False), {})


class CorrectionAdminTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.match, _ = make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, **kw):
        data = {"target": "match", "key": "022-3", "field": "spectators", "value": "2000", "reason": "기록실 확인",
                "is_active": "on", **kw}
        return self.client.post("/admin/ops/correction/add/", data)

    def test_add_applies_at_once_and_turning_off_restores(self):
        r = self.add()
        self.assertEqual(r.status_code, 302, r.content[:500])
        c = Correction.objects.get()
        self.assertEqual((c.source_value, c.created_by), ("1348", self.admin))
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 2000)

        # changing the value keeps the original source value
        self.client.post(f"/admin/ops/correction/{c.pk}/change/", {
            "target": "match", "key": "022-3", "field": "spectators", "value": "2100", "reason": "다시 확인",
            "is_active": "on"})
        c.refresh_from_db()
        self.assertEqual((c.value, c.source_value), ("2100", "1348"))
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 2100)

        self.client.post("/admin/ops/correction/", {"action": "deactivate", "_selected_action": [c.pk]})
        c.refresh_from_db()
        self.assertFalse(c.is_active)
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 1348)

    def test_rejected_correction_changes_nothing(self):
        r = self.add(value="abc")
        self.assertEqual(r.status_code, 200)  # form shown again with the error
        self.assertFalse(Correction.objects.exists())
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 1348)

    def test_team_stat_correction_recomputes_opponent_errors(self):
        self.add(target="team_match_stat", key="022-3/1009", field="points", value="90")
        t = TeamMatchStat.objects.get(match=self.match, team__code="1009")
        self.assertEqual((t.points, t.opponent_errors), (90, 55))  # 90 - attack 35

    def test_player_name_and_set_score(self):
        self.add(target="player", key="1000999", field="name", value="벤치 선수(B)")
        self.add(target="set_score", key="022-3/1", field="home_points", value="25")
        self.assertEqual(Player.objects.get(code="1000999").name, "벤치 선수(B)")
        self.assertEqual(SetScore.objects.get(match=self.match, set_no=1).home_points, 25)

    def test_add_form_is_prefilled_from_the_quality_page(self):
        r = self.client.get("/admin/ops/correction/add/?target=set_score&key=022-3/1")
        self.assertContains(r, 'value="022-3/1"')
        self.assertContains(r, "고칠 수 있는 항목")


class QualityIssueTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.match, _ = make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")
        kw = {"season_code": "022", "game_no": 3, "match": cls.match}
        cls.rally3 = QualityIssue.objects.create(**kw, set_no=3, rule="MISSING_RALLY", severity="ERROR")
        QualityIssue.objects.create(**kw, set_no=2, rule="MISSING_RALLY", severity="ERROR", status="ACKNOWLEDGED")
        QualityIssue.objects.create(**kw, rule="MISSING_BOX_SCORE", severity="ERROR", status="RESOLVED")
        QualityIssue.objects.create(**kw, rule="POINTS_MISMATCH", subject="1006", severity="WARNING")

    def test_match_page_lists_unresolved_errors_by_rule(self):
        Correction.objects.create(target="set_score", key="022-3/1", field="duration_min", value="30", reason="x")
        Correction.objects.create(target="match", key="022-30", field="venue", value="x", reason="other match")
        notes = self.client.get("/api/matches/022-3").json()["data_notes"]
        self.assertEqual(notes, {"issues": [{"rule": "MISSING_RALLY", "label": "문자중계 없음", "sets": [2, 3]}],
                                 "corrected": 1})

    def test_acknowledge_and_reopen(self):
        self.client.force_login(self.admin)
        self.client.post("/admin/ops/qualityissue/", {"action": "acknowledge", "_selected_action": [self.rally3.pk]})
        self.rally3.refresh_from_db()
        self.assertEqual((self.rally3.status, self.rally3.acknowledged_by), ("ACKNOWLEDGED", self.admin))
        self.client.post("/admin/ops/qualityissue/", {"action": "reopen", "_selected_action": [self.rally3.pk]})
        self.rally3.refresh_from_db()
        self.assertEqual(self.rally3.status, "OPEN")

    def test_admin_pages_render(self):
        self.client.force_login(self.admin)
        r = self.client.get("/admin/ops/qualityissue/?status__exact=OPEN")
        self.assertContains(r, "022-3 · 3세트")
        r = self.client.get(f"/admin/ops/qualityissue/{self.rally3.pk}/change/")
        self.assertContains(r, "확인함")  # play-by-play cannot be corrected, only acknowledged
        bad_set = QualityIssue.objects.create(season_code="022", game_no=3, match=self.match, set_no=1,
                                              rule="INVALID_SET_SCORE", severity="ERROR")
        r = self.client.get(f"/admin/ops/qualityissue/{bad_set.pk}/change/")
        self.assertContains(r, "target=set_score&amp;key=022-3/1")
