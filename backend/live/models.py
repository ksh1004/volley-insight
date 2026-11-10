"""실시간 경기 상태, 세트별 지표, 득점 하나하나의 흐름(설계서 ERD 2.2, PIPE-06).

Flink 작업 `live-metrics`(flink_jobs/live_metrics.py)만 SQL upsert로 쓰고, 웹 앱은 읽기만 한다.
경기를 실시간으로 따라가거나 재생(PIPE-11)하는 동안 행이 생긴다. `mode`로 둘을 구별하므로 끝난 경기의 재생이
진짜 실시간 경기로 보이는 일은 없다. 경기가 CONFIRMED가 되면 경기 화면은 공식 기록(rally_event, team_match_stat)을
쓰고, 이 행들은 마지막 실시간 상태로 남는다.
"""
from django.db import models

from league.models import PipelineModel, Player, Team
from matches.models import Match


class LiveMode(models.TextChoices):
    """실시간 경기인지, 끝난 경기의 재생인지."""
    LIVE = "LIVE", "실시간"
    REPLAY = "REPLAY", "재생"


class LiveMatchState(PipelineModel):
    """경기당 한 행: 지금 점수·세트·서브권·연속 득점·승리 확률·마지막 득점. 실시간 화면의 스코어보드가 된다."""

    class Status(models.TextChoices):
        """진행 상태."""
        LIVE = "LIVE", "진행 중"
        FINISHED = "FINISHED", "종료"

    match = models.OneToOneField(Match, on_delete=models.CASCADE, primary_key=True, related_name="live_state")
    mode = models.CharField(max_length=6, choices=LiveMode.choices)
    session = models.CharField(max_length=40, help_text="'live', or the run id of a replay")
    status = models.CharField(max_length=8, choices=Status.choices)
    current_set = models.PositiveSmallIntegerField(default=0)
    home_points = models.PositiveSmallIntegerField(default=0)
    away_points = models.PositiveSmallIntegerField(default=0)
    home_sets = models.PositiveSmallIntegerField(default=0)
    away_sets = models.PositiveSmallIntegerField(default=0)
    set_scores = models.JSONField(default=list, help_text="finished sets: [[home, away], ...]")
    serving_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name="+")
    home_run = models.PositiveSmallIntegerField(default=0, help_text="points in a row (연속 득점)")
    away_run = models.PositiveSmallIntegerField(default=0)
    home_set_win_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, help_text="ANL-07")
    home_win_prob = models.DecimalField(max_digits=5, decimal_places=4, null=True, help_text="ANL-07")
    last_play = models.JSONField(null=True, help_text="the latest point, same shape as a LiveRally row")
    last_event_seq = models.IntegerField(default=0, help_text="set_no * 10000 + position in the set's feed")
    fetched_at = models.DateTimeField(null=True, help_text="when the collector fetched the latest events")
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "live_match_state"
        indexes = [models.Index(fields=["status", "updated_at"], name="ix_live_state_status")]


class LiveSetMetric(PipelineModel):
    """세트 × 팀 한 행: 득점, 공격 시도·성공·성공률, 서브 에이스·범실, 블로킹, 범실(실시간 화면의 세트별 비교표)."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="live_set_metrics")
    set_no = models.PositiveSmallIntegerField()
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    points = models.PositiveSmallIntegerField(default=0)
    attack_attempts = models.PositiveSmallIntegerField(default=0)
    attack_success = models.PositiveSmallIntegerField(default=0)
    attack_success_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    serve_aces = models.PositiveSmallIntegerField(default=0)
    serve_errors = models.PositiveSmallIntegerField(default=0)
    block_points = models.PositiveSmallIntegerField(default=0)
    errors = models.PositiveSmallIntegerField(default=0, help_text="범실: events with an er* result")

    class Meta:
        db_table = "live_set_metric"
        constraints = [models.UniqueConstraint(fields=["match", "set_no", "team"], name="uq_live_set_metric")]


class LiveRally(PipelineModel):
    """득점 하나당 한 행: 득점 뒤 점수, 서브한 팀, 딴 팀과 방법(공격·블로킹·서브·상대 범실), 관련 선수.
    점수 흐름 차트와 득점 피드에 쓴다."""

    class Kind(models.TextChoices):
        """득점 종류."""
        ATTACK = "ATTACK", "공격"
        BLOCK = "BLOCK", "블로킹"
        ACE = "ACE", "서브"
        OPP_ERROR = "OPP_ERROR", "상대 범실"
        OTHER = "OTHER", "기타"

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="live_rallies")
    set_no = models.PositiveSmallIntegerField()
    point_no = models.PositiveSmallIntegerField(help_text="home + away score after this point")
    rally_no = models.PositiveSmallIntegerField(help_text="KOVO rpage")
    seq = models.IntegerField()
    home_score = models.PositiveSmallIntegerField()
    away_score = models.PositiveSmallIntegerField()
    winner_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    server_team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    player = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, related_name="+",
                               help_text="scorer, or the player who made the error for OPP_ERROR")
    player_name = models.CharField(max_length=30, blank=True)
    action_code = models.CharField(max_length=2, blank=True)
    result_code = models.CharField(max_length=3, blank=True)

    class Meta:
        db_table = "live_rally"
        ordering = ["set_no", "point_no"]
        constraints = [models.UniqueConstraint(fields=["match", "set_no", "point_no"], name="uq_live_rally")]
