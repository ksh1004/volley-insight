"""데이터 품질 문제(PIPE-10)와 수동 수정(ADM-03) 테스트: 입력 검사, 관리자 화면에서의 적용·되돌리기, 경기 화면 안내.

  docker compose exec -T django-api python manage.py test ops

수동 수정(Correction) = "대상 테이블 + 키 + 필드 + 새 값 + 이유". 저장하면 정제 테이블에 바로 적용되고,
끄면(비활성화) 원래 수집된 값(source_value)으로 되돌린다. 다음 정제 실행 때도 다시 적용된다.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from league.models import Player
from matches.models import Match, SetScore, TeamMatchStat
from matches.tests import make_league

from .models import Correction, QualityIssue


def correction(**kw):
    """기본값: 022-3 경기의 관중 수를 2000으로 고치는 수정. kw로 일부만 바꿔 쓴다(저장은 하지 않음)."""
    return Correction(**{"target": "match", "key": "022-3", "field": "spectators", "value": "2000",
                         "reason": "KOVO 기록실 확인", **kw})


class CorrectionValidationTest(TestCase):
    """수정 입력 검사(full_clean): 고칠 수 있는 필드인지, 키가 실제 행을 가리키는지, 값이 형식에 맞는지."""

    @classmethod
    def setUpTestData(cls):
        make_league()

    def errors(self, **kw):
        """검사 오류를 {필드: [메시지]}로 돌려준다. 문제가 없으면 {}."""
        try:
            correction(**kw).full_clean()
        except ValidationError as e:
            return e.message_dict
        return {}

    def test_valid_corrections(self):
        """올바른 수정들: 경기 관중 수, 세트 점수, 선수 포지션, 선수 키를 비우기."""
        self.assertEqual(self.errors(), {})
        self.assertEqual(self.errors(target="set_score", key="022-3/2", field="home_points", value="25"), {})
        self.assertEqual(self.errors(target="player", key="1000525", field="position", value="OH"), {})
        self.assertEqual(self.errors(target="player", key="1000525", field="height_cm", value=""), {})  # 빈 값 = NULL

    def test_unknown_field_key_or_bad_value(self):
        """고칠 수 없는 필드, 없는 행을 가리키는 키, 형식에 안 맞는 값은 해당 필드의 오류로 알려 준다."""
        self.assertIn("field", self.errors(field="status"))                       # 파이프라인이 계산하는 값이라 못 고침
        self.assertIn("key", self.errors(key="022-999"))
        self.assertIn("key", self.errors(target="set_score", key="022-3", field="home_points"))  # 세트 번호 없음
        self.assertIn("key", self.errors(target="player_match_stat", key="022-3/0000001"))
        self.assertIn("value", self.errors(value="많음"))
        self.assertIn("value", self.errors(value="-5"))                          # 0 이상이어야 하는 필드
        self.assertIn("value", self.errors(target="player", key="1000525", field="position", value="XX"))
        self.assertIn("value", self.errors(target="set_score", key="022-3/1", field="home_points", value=""))

    def test_one_active_correction_per_field(self):
        """같은 행·필드에 켜진 수정은 하나만 둘 수 있다(꺼진 수정은 여러 개 가능)."""
        correction().save()
        self.assertIn("__all__", self.errors(value="3000"))
        self.assertEqual(self.errors(value="3000", is_active=False), {})


class CorrectionAdminTest(TestCase):
    """관리자 화면에서 수정을 추가·변경·끄기 했을 때 정제 테이블에 바로 반영되는지."""

    @classmethod
    def setUpTestData(cls):
        cls.match, _ = make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, **kw):
        """관리자 추가 화면으로 수정을 제출한다(기본: 022-3 관중 수 2000)."""
        data = {"target": "match", "key": "022-3", "field": "spectators", "value": "2000", "reason": "기록실 확인",
                "is_active": "on", **kw}
        return self.client.post("/admin/ops/correction/add/", data)

    def test_add_applies_at_once_and_turning_off_restores(self):
        """추가하면 바로 적용되고 원래 값(1348)을 기억한다. 값을 바꿔도 원래 값은 그대로, 끄면 원래 값으로 돌아간다."""
        r = self.add()
        self.assertEqual(r.status_code, 302, r.content[:500])
        c = Correction.objects.get()
        self.assertEqual((c.source_value, c.created_by), ("1348", self.admin))
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 2000)

        # 값을 다시 바꿔도 처음 수집된 원래 값은 그대로 남는다
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
        """검사에 걸린 수정은 저장도 적용도 되지 않는다."""
        r = self.add(value="abc")
        self.assertEqual(r.status_code, 200)  # 오류와 함께 폼을 다시 보여 줌
        self.assertFalse(Correction.objects.exists())
        self.assertEqual(Match.objects.get(pk=self.match.pk).spectators, 1348)

    def test_team_stat_correction_recomputes_opponent_errors(self):
        """팀 득점을 고치면 그로부터 계산되는 상대 범실 득점도 다시 계산한다(득점 - 공격·블로킹·서브 득점)."""
        self.add(target="team_match_stat", key="022-3/1009", field="points", value="90")
        t = TeamMatchStat.objects.get(match=self.match, team__code="1009")
        self.assertEqual((t.points, t.opponent_errors), (90, 55))  # 90 - 공격 득점 35

    def test_player_name_and_set_score(self):
        """선수 이름과 세트 점수도 고칠 수 있다."""
        self.add(target="player", key="1000999", field="name", value="벤치 선수(B)")
        self.add(target="set_score", key="022-3/1", field="home_points", value="25")
        self.assertEqual(Player.objects.get(code="1000999").name, "벤치 선수(B)")
        self.assertEqual(SetScore.objects.get(match=self.match, set_no=1).home_points, 25)

    def test_add_form_is_prefilled_from_the_quality_page(self):
        """품질 문제 화면의 "고치기" 링크(?target=&key=)로 열면 대상·키가 채워져 있고 고칠 수 있는 항목이 안내된다."""
        r = self.client.get("/admin/ops/correction/add/?target=set_score&key=022-3/1")
        self.assertContains(r, 'value="022-3/1"')
        self.assertContains(r, "고칠 수 있는 항목")


class QualityIssueTest(TestCase):
    """품질 문제: 경기 화면의 데이터 안내, 관리자의 확인·다시 열기, 관리자 화면 표시."""

    @classmethod
    def setUpTestData(cls):
        """022-3 경기의 품질 문제 4개: 3세트 랠리 기록 없음(OPEN), 2세트 랠리 기록 없음(확인함),
        박스스코어 없음(해결됨), 점수 불일치 경고(OPEN)."""
        cls.match, _ = make_league()
        cls.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw-for-tests-1")
        kw = {"season_code": "022", "game_no": 3, "match": cls.match}
        cls.rally3 = QualityIssue.objects.create(**kw, set_no=3, rule="MISSING_RALLY", severity="ERROR")
        QualityIssue.objects.create(**kw, set_no=2, rule="MISSING_RALLY", severity="ERROR", status="ACKNOWLEDGED")
        QualityIssue.objects.create(**kw, rule="MISSING_BOX_SCORE", severity="ERROR", status="RESOLVED")
        QualityIssue.objects.create(**kw, rule="POINTS_MISMATCH", subject="1006", severity="WARNING")

    def test_match_page_lists_unresolved_errors_by_rule(self):
        """경기 API의 data_notes: 해결되지 않은 오류를 규칙별로 묶어 세트 목록과 함께 보여 주고(경고·해결됨은 제외),
        이 경기에 적용된 수정 수를 센다(다른 경기의 수정은 제외)."""
        Correction.objects.create(target="set_score", key="022-3/1", field="duration_min", value="30", reason="x")
        Correction.objects.create(target="match", key="022-30", field="venue", value="x", reason="other match")
        notes = self.client.get("/api/matches/022-3").json()["data_notes"]
        self.assertEqual(notes, {"issues": [{"rule": "MISSING_RALLY", "label": "문자중계 없음", "sets": [2, 3]}],
                                 "corrected": 1})

    def test_acknowledge_and_reopen(self):
        """관리자가 "확인함"으로 바꾸면 확인자가 기록되고, "다시 열기"로 OPEN으로 되돌릴 수 있다."""
        self.client.force_login(self.admin)
        self.client.post("/admin/ops/qualityissue/", {"action": "acknowledge", "_selected_action": [self.rally3.pk]})
        self.rally3.refresh_from_db()
        self.assertEqual((self.rally3.status, self.rally3.acknowledged_by), ("ACKNOWLEDGED", self.admin))
        self.client.post("/admin/ops/qualityissue/", {"action": "reopen", "_selected_action": [self.rally3.pk]})
        self.rally3.refresh_from_db()
        self.assertEqual(self.rally3.status, "OPEN")

    def test_admin_pages_render(self):
        """목록에 "경기 · 세트"가 보이고, 랠리 기록 문제는 확인만 가능하며, 세트 점수 문제에는 수정 링크가 있다."""
        self.client.force_login(self.admin)
        r = self.client.get("/admin/ops/qualityissue/?status__exact=OPEN")
        self.assertContains(r, "022-3 · 3세트")
        r = self.client.get(f"/admin/ops/qualityissue/{self.rally3.pk}/change/")
        self.assertContains(r, "확인함")  # 랠리 기록은 고칠 수 없고 확인만 할 수 있다
        bad_set = QualityIssue.objects.create(season_code="022", game_no=3, match=self.match, set_no=1,
                                              rule="INVALID_SET_SCORE", severity="ERROR")
        r = self.client.get(f"/admin/ops/qualityissue/{bad_set.pk}/change/")
        self.assertContains(r, "target=set_score&amp;key=022-3/1")
