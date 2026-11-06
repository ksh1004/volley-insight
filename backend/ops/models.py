"""Data quality issues (PIPE-10) and manual data corrections (ADM-03).

QualityIssue rows are written by the curation job (spark_jobs/curate_kovo.py -> sql/merge_kovo.sql), not by the
web app. An admin only changes their status (acknowledge a gap in KOVO's own data, reopen).
Correction rows are written by admins in Django admin. The curation job applies every active correction to the
curated data before its checks, so a correction survives re-collection and also reaches the marts.
"""
from django.conf import settings
from django.db import models
from django.db.models.functions import Now

from matches.models import Match


class QualityIssue(models.Model):
    class Rule(models.TextChoices):
        # ERROR: the match stays FINISHED (not CONFIRMED) until it is fixed
        SET_COUNT_MISMATCH = "SET_COUNT_MISMATCH", "세트 스코어와 세트별 점수 불일치"
        MISSING_BOX_SCORE = "MISSING_BOX_SCORE", "선수 기록 없음"
        MISSING_RALLY = "MISSING_RALLY", "문자중계 없음"
        RALLY_SCORE_MISMATCH = "RALLY_SCORE_MISMATCH", "문자중계 점수와 공식 점수 불일치"
        INVALID_SET_SCORE = "INVALID_SET_SCORE", "세트 점수가 경기 규칙에 맞지 않음"
        STAT_RANGE = "STAT_RANGE", "기록 범위 오류(성공이 시도보다 많음 등)"
        MISSING_VALUE = "MISSING_VALUE", "필수 값 누락"
        # WARNING: shown to admins, the match can still be CONFIRMED
        BOX_SCORE_MISMATCH = "BOX_SCORE_MISMATCH", "팀 합계와 선수 합 불일치"
        POINTS_MISMATCH = "POINTS_MISMATCH", "팀 득점과 세트 점수 합 불일치"
        PLAYER_POINTS_MISMATCH = "PLAYER_POINTS_MISMATCH", "선수 득점과 공격·블로킹·서브 합 불일치"
        PLAYER_NAME_MISSING = "PLAYER_NAME_MISSING", "선수 이름 없음"

    class Severity(models.TextChoices):
        ERROR = "ERROR", "오류"
        WARNING = "WARNING", "경고"

    class Status(models.TextChoices):
        OPEN = "OPEN", "미해결"
        ACKNOWLEDGED = "ACKNOWLEDGED", "확인함"   # e.g. KOVO itself has no data; the next runs keep this status
        RESOLVED = "RESOLVED", "해결됨"           # no longer found by the last run of its season

    season_code = models.CharField("시즌", max_length=3)
    game_no = models.PositiveIntegerField("경기 번호")
    # null only when the match itself could not be loaded (e.g. a missing team code)
    match = models.ForeignKey(Match, on_delete=models.CASCADE, null=True, blank=True, related_name="quality_issues",
                              verbose_name="경기")
    set_no = models.PositiveSmallIntegerField("세트", default=0, help_text="0 = 경기 전체")
    rule = models.CharField("검사", max_length=30, choices=Rule.choices)
    subject = models.CharField("대상 코드", max_length=20, blank=True, help_text="문제가 있는 팀·선수 코드")
    detail = models.TextField("내용", blank=True)
    severity = models.CharField("심각도", max_length=10, choices=Severity.choices)
    status = models.CharField("상태", max_length=15, choices=Status.choices, default=Status.OPEN, db_default=Status.OPEN)
    note = models.TextField("메모", blank=True, help_text="예: KOVO 기록실에도 없음(2026-11-05 확인)")
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+", verbose_name="확인한 사람")
    first_seen_at = models.DateTimeField("처음 발견", db_default=Now(), editable=False)
    last_seen_at = models.DateTimeField("마지막 발견", db_default=Now(), editable=False)
    resolved_at = models.DateTimeField("해결", null=True, blank=True, editable=False)

    class Meta:
        db_table = "quality_issue"
        verbose_name = verbose_name_plural = "품질 검사 결과"
        ordering = ["-season_code", "-game_no", "set_no", "rule"]
        constraints = [models.UniqueConstraint(fields=["season_code", "game_no", "set_no", "rule", "subject"],
                                               name="uq_quality_issue")]
        indexes = [models.Index(fields=["status", "severity"], name="ix_quality_issue_status")]

    def __str__(self):
        where = f"{self.season_code}-{self.game_no}" + (f" {self.set_no}세트" if self.set_no else "")
        return f"{where} {self.get_rule_display()}"


class Correction(models.Model):
    """One field of one curated row, set by an admin. See ops/corrections.py for targets, keys and fields."""

    class Target(models.TextChoices):
        PLAYER = "player", "선수"
        TEAM = "team", "팀"
        MATCH = "match", "경기"
        SET_SCORE = "set_score", "세트 점수"
        PLAYER_STAT = "player_match_stat", "선수 경기 기록"
        TEAM_STAT = "team_match_stat", "팀 경기 기록"

    target = models.CharField("대상", max_length=20, choices=Target.choices)
    key = models.CharField("키", max_length=30, help_text="선수·팀: KOVO 코드 / 경기: 023-5 / 세트: 023-5/3 / "
                                                    "선수·팀 경기 기록: 023-5/선수·팀 코드")
    field = models.CharField("항목", max_length=30)
    value = models.CharField("고친 값", max_length=100, blank=True, help_text="비우면 값 없음(숫자·날짜) 또는 빈 글자")
    source_value = models.CharField("원래 값", max_length=100, blank=True, editable=False,
                                    help_text="처음 보정하기 전 값. 보정을 끄면 이 값으로 되돌립니다")
    reason = models.TextField("근거", help_text="무엇이 왜 틀렸는지, 근거(KOVO 공식 기록 화면, 기사 등)")
    is_active = models.BooleanField("켜짐", default=True, help_text="끄면 원래 값으로 되돌립니다")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+", editable=False, verbose_name="만든 사람")
    created_at = models.DateTimeField("만든 시각", auto_now_add=True)
    updated_at = models.DateTimeField("고친 시각", auto_now=True)

    class Meta:
        db_table = "data_correction"
        verbose_name = verbose_name_plural = "데이터 보정"
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["target", "key", "field"], condition=models.Q(is_active=True),
                                               name="uq_active_correction")]

    def __str__(self):
        return f"{self.get_target_display()} {self.key} {self.field} = {self.value!r}"

    def clean(self):
        from .corrections import validate

        validate(self)


class JobAlert(models.Model):
    """A pipeline job that failed for good (PIPE-09). Written by the Airflow failure callbacks
    (airflow/dags/kovo_tasks/alerts.py), not by the web app; an admin marks it checked."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "미확인"
        ACKNOWLEDGED = "ACKNOWLEDGED", "확인함"

    dag_id = models.CharField("작업(DAG)", max_length=100)
    task_id = models.CharField("단계", max_length=200, blank=True, help_text="비어 있으면 실행 전체가 실패한 경우")
    run_id = models.CharField("실행", max_length=250)
    map_index = models.IntegerField("나눈 번호", default=-1, db_default=-1)
    try_number = models.IntegerField("시도", null=True, blank=True)
    error = models.TextField("오류", blank=True, default="", db_default="")
    log_url = models.CharField("Airflow 로그", max_length=500, blank=True, default="", db_default="")
    delivery = models.CharField("알림 전송", max_length=200, blank=True, default="", db_default="",
                                help_text="SENT: 메신저로 보냄 / NO_WEBHOOK: 웹훅 주소 없음 / REPEAT: 같은 작업의 "
                                          "미확인 알림이 최근에 있어 다시 보내지 않음 / FAILED: 보내지 못함")
    status = models.CharField("상태", max_length=12, choices=Status.choices, default=Status.OPEN,
                              db_default=Status.OPEN)
    note = models.TextField("메모", blank=True, default="", db_default="")
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+", verbose_name="확인한 사람")
    created_at = models.DateTimeField("실패 시각", db_default=Now())

    class Meta:
        db_table = "job_alert"
        verbose_name = "작업 실패"
        verbose_name_plural = "수집 현황"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"], name="ix_job_alert_status"),
                   models.Index(fields=["dag_id", "run_id"], name="ix_job_alert_run")]

    def __str__(self):
        return f"{self.dag_id} {self.task_id or '(실행 전체)'} {self.created_at:%m-%d %H:%M}"


class ManualRun(models.Model):
    """A pipeline job started from the admin page (ADM-02). The run itself lives in Airflow."""

    dag_id = models.CharField("작업(DAG)", max_length=100)
    conf = models.JSONField("설정", default=dict, blank=True)
    note = models.CharField("메모", max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                     related_name="+", verbose_name="실행한 사람")
    requested_at = models.DateTimeField("요청 시각", auto_now_add=True)
    run_id = models.CharField("Airflow 실행", max_length=250, blank=True)

    class Meta:
        db_table = "manual_run"
        verbose_name = verbose_name_plural = "수동 실행"
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.dag_id} {self.requested_at:%m-%d %H:%M}"
