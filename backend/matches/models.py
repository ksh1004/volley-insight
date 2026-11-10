"""경기, 세트 점수, 박스스코어, 문자중계 모델(설계서 ERD 2.1).

Spark 정제 작업(spark_jobs/curate_kovo.py)이 KOVO 원본 응답으로 채운다.
필드 이름과 KOVO 필드의 대응은 주석에 적었다. 어떻게 확인했는지는 collectors/README.md 참고.
"""
from django.db import models

from league.models import Competition, Gender, PipelineModel, Player, Team


class Match(PipelineModel):
    """경기 하나. 공개 키는 '시즌 코드-경기 번호'(matches.serializers.match_key). 세트 수는 홈/원정이 이긴 세트."""

    class Status(models.TextChoices):
        """경기 상태. SCHEDULED → (LIVE) → FINISHED → CONFIRMED 순서로 바뀐다."""
        SCHEDULED = "SCHEDULED", "예정"
        LIVE = "LIVE", "진행 중"
        FINISHED = "FINISHED", "종료"          # 공식 결과가 있음
        CONFIRMED = "CONFIRMED", "기록 확정"    # 박스스코어 + 문자중계를 적재하고 품질 검사까지 통과
        CANCELLED = "CANCELLED", "미진행"      # 예정일 2일 뒤에도 결과 없음(시리즈가 일찍 끝난 경우 등)

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
    """세트 하나의 점수와 경기 시간(분)."""
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
    """팀과 선수 박스스코어가 함께 쓰는 기록 컬럼(추상 모델). 주석은 KOVO 필드 이름.
    공격: 시도/성공/상대 블로킹에 막힘/범실, 서브: 시도/에이스/범실, 블로킹: 시도/성공/유효/어시스트,
    리시브: 시도/정확/실패, 디그: 시도/성공, 세트(토스): 시도/성공."""

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
    """경기 하나에서 팀 하나의 기록."""
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="team_stats")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="match_stats")
    points = models.PositiveSmallIntegerField(default=0, help_text="all points incl. opponent errors (point)")
    opponent_errors = models.PositiveSmallIntegerField(default=0, help_text="points - attack - block - ace")

    class Meta:
        db_table = "team_match_stat"
        constraints = [models.UniqueConstraint(fields=["match", "team"], name="uq_team_match_stat")]
        indexes = [models.Index(fields=["team"], name="ix_team_match_stat_team")]


class PlayerMatchStat(BoxScore):
    """경기 하나에서 선수 하나의 기록. played_sets는 세트별 출전 여부를 '1'/'0'으로 이은 문자열."""
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
    """선수 기록의 공격 종류별 세부(오픈, 시간차, 이동, 후위, 속공, 퀵오픈).
    KOVO 필드 접두사: oa 오픈, ta 시간차, ma 이동, ba 후위, sa 속공, csa 퀵오픈."""

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
    """문자중계 이벤트 하나(서브, 리시브, 공격, 득점, 교체 등). 점수는 이 이벤트 직후의 누적 점수로 Spark가 계산한다.
    동작·결과 코드표는 요구사항 부록 A(화면에서는 frontend/src/constants/volley.js)."""

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
