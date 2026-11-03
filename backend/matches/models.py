"""Matches, set scores, box scores and play-by-play (design.md ERD 2.1).

Filled by the Spark curation job (spark_jobs/curate_kovo.py) from the raw KOVO responses.
Field names map to KOVO fields as noted; see collectors/README.md for how they were verified.
"""
from django.db import models

from league.models import Competition, Gender, PipelineModel, Player, Team


class Match(PipelineModel):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "예정"
        LIVE = "LIVE", "진행 중"
        FINISHED = "FINISHED", "종료"          # official result exists
        CONFIRMED = "CONFIRMED", "기록 확정"    # box score + play-by-play loaded and checked

    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="matches")
    game_no = models.PositiveIntegerField(help_text="KOVO gnum, unique within a season")
    round = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    home_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="home_matches")
    away_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="away_matches")
    scheduled_at = models.DateTimeField()
    venue = models.CharField(max_length=50, blank=True)
    city = models.CharField(max_length=30, blank=True)
    broadcasters = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    home_sets = models.PositiveSmallIntegerField(default=0)
    away_sets = models.PositiveSmallIntegerField(default=0)
    duration_min = models.PositiveSmallIntegerField(null=True, blank=True)
    spectators = models.PositiveIntegerField(null=True, blank=True)
    referee_main = models.CharField(max_length=30, blank=True)
    referee_sub = models.CharField(max_length=30, blank=True)

    class Meta:
        db_table = "match"
        ordering = ["scheduled_at"]
        constraints = [models.UniqueConstraint(fields=["competition", "game_no"], name="uq_match")]
        indexes = [
            models.Index(fields=["scheduled_at"], name="ix_match_scheduled"),
            models.Index(fields=["home_team", "scheduled_at"], name="ix_match_home"),
            models.Index(fields=["away_team", "scheduled_at"], name="ix_match_away"),
            models.Index(fields=["status"], name="ix_match_status"),
        ]

    def __str__(self):
        return f"{self.competition.season.code}-{self.game_no} {self.home_team} vs {self.away_team}"


class SetScore(PipelineModel):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="sets")
    set_no = models.PositiveSmallIntegerField()
    home_points = models.PositiveSmallIntegerField()
    away_points = models.PositiveSmallIntegerField()
    duration_min = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "set_score"
        ordering = ["match", "set_no"]
        constraints = [models.UniqueConstraint(fields=["match", "set_no"], name="uq_set_score")]


class BoxScore(PipelineModel):
    """Columns shared by team and player box scores. KOVO field in brackets."""

    attack_attempts = models.PositiveSmallIntegerField(default=0)   # att
    attack_success = models.PositiveSmallIntegerField(default=0)    # ats
    attack_blocked = models.PositiveSmallIntegerField(default=0)    # atf
    attack_errors = models.PositiveSmallIntegerField(default=0)     # ate
    serve_attempts = models.PositiveSmallIntegerField(default=0)    # st
    serve_aces = models.PositiveSmallIntegerField(default=0)        # ss
    serve_errors = models.PositiveSmallIntegerField(default=0)      # sf
    block_attempts = models.PositiveSmallIntegerField(default=0)    # bt
    block_success = models.PositiveSmallIntegerField(default=0)     # bs
    block_effective = models.PositiveSmallIntegerField(default=0)   # blkvb (유효 블로킹)
    block_assist = models.PositiveSmallIntegerField(default=0)      # blkas
    receive_attempts = models.PositiveSmallIntegerField(default=0)  # rt
    receive_excellent = models.PositiveSmallIntegerField(default=0) # rs (정확)
    receive_errors = models.PositiveSmallIntegerField(default=0)    # rf
    dig_attempts = models.PositiveSmallIntegerField(default=0)      # dt
    dig_success = models.PositiveSmallIntegerField(default=0)       # ds
    set_attempts = models.PositiveSmallIntegerField(default=0)      # sett
    set_success = models.PositiveSmallIntegerField(default=0)       # sets
    errors = models.PositiveSmallIntegerField(default=0)            # err (범실 합계)
    warnings = models.PositiveSmallIntegerField(default=0)          # warning

    class Meta:
        abstract = True


class TeamMatchStat(BoxScore):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="team_stats")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="match_stats")
    points = models.PositiveSmallIntegerField(default=0, help_text="all points incl. opponent errors (point)")
    opponent_errors = models.PositiveSmallIntegerField(default=0, help_text="points - attack - block - ace")

    class Meta:
        db_table = "team_match_stat"
        constraints = [models.UniqueConstraint(fields=["match", "team"], name="uq_team_match_stat")]
        indexes = [models.Index(fields=["team"], name="ix_team_match_stat_team")]


class PlayerMatchStat(BoxScore):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="player_stats")
    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="match_stats")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="player_match_stats")
    back_number = models.PositiveSmallIntegerField(null=True, blank=True)  # bnum
    position = models.CharField(max_length=2, blank=True)
    is_captain = models.BooleanField(default=False)
    played_sets = models.CharField(max_length=5, help_text="1 if the player appeared in that set, e.g. 11100")
    points = models.PositiveSmallIntegerField(default=0, help_text="attack + block + ace (point)")

    class Meta:
        db_table = "player_match_stat"
        constraints = [models.UniqueConstraint(fields=["match", "player"], name="uq_player_match_stat")]
        indexes = [models.Index(fields=["player", "match"], name="ix_player_match_stat_player")]


class PlayerAttackStat(PipelineModel):
    """Attack by type. KOVO prefix: oa OPEN, ta TIME_DIFF, ma MOVING, ba BACK, sa QUICK, csa QUICK_OPEN."""

    class AttackType(models.TextChoices):
        OPEN = "OPEN", "오픈"
        TIME_DIFF = "TIME_DIFF", "시간차"
        MOVING = "MOVING", "이동"
        BACK = "BACK", "후위"
        QUICK = "QUICK", "속공"
        QUICK_OPEN = "QUICK_OPEN", "퀵오픈"

    player_match_stat = models.ForeignKey(PlayerMatchStat, on_delete=models.CASCADE, related_name="attacks")
    attack_type = models.CharField(max_length=10, choices=AttackType.choices)
    attempts = models.PositiveSmallIntegerField(default=0)
    success = models.PositiveSmallIntegerField(default=0)
    blocked = models.PositiveSmallIntegerField(default=0)
    errors = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "player_attack_stat"
        constraints = [models.UniqueConstraint(fields=["player_match_stat", "attack_type"], name="uq_player_attack_stat")]


class RallyEvent(PipelineModel):
    """One play-by-play event. Scores are the running score after this event, computed by Spark."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="rally_events")
    set_no = models.PositiveSmallIntegerField()
    event_no = models.PositiveSmallIntegerField(help_text="order within the set, from 1")
    rally_no = models.PositiveSmallIntegerField(help_text="KOVO rpage: one rally, substitution or time-out")
    seq = models.PositiveSmallIntegerField(help_text="KOVO rindex: order within rally_no")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    player = models.ForeignKey(Player, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    back_number = models.PositiveSmallIntegerField(null=True, blank=True)
    action_code = models.CharField(max_length=2, help_text="requirements appendix A.1")
    result_code = models.CharField(max_length=3, help_text="requirements appendix A.2")
    grade = models.SmallIntegerField(default=0, help_text="1 point won by this team, -1 point lost, 0 none")
    home_score = models.PositiveSmallIntegerField()
    away_score = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "rally_event"
        ordering = ["match", "set_no", "event_no"]
        constraints = [models.UniqueConstraint(fields=["match", "set_no", "event_no"], name="uq_rally_event")]
        indexes = [
            models.Index(fields=["match", "set_no"], name="ix_rally_event_match_set"),
            models.Index(fields=["player"], name="ix_rally_event_player"),
        ]
