"""Live match state, per-set indicators and the point-by-point flow (design.md ERD 2.2, PIPE-06).

Written only by the Flink job `live-metrics` (flink_jobs/live_metrics.py) with SQL upserts; the web app reads them.
A row exists while a match is followed live or replayed (PIPE-11). `mode` tells the two apart, so a replay of a
finished match is never shown as a real live game. Once the match is CONFIRMED, its pages use the official
records (rally_event, team_match_stat) instead; these rows stay as the last live picture.
"""
from django.db import models

from league.models import PipelineModel, Player, Team
from matches.models import Match


class LiveMode(models.TextChoices):
    LIVE = "LIVE", "실시간"
    REPLAY = "REPLAY", "재생"


class LiveMatchState(PipelineModel):
    class Status(models.TextChoices):
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
    """One row per point: the score after it, who served, who won it and how (점수 추이, 연속 득점 흐름)."""

    class Kind(models.TextChoices):
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
