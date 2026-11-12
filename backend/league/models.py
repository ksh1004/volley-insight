"""시즌, 대회, 팀, 선수 모델(설계서 ERD 2.1).

이 테이블들은 웹 앱이 아니라 Spark 정제 작업(spark_jobs/curate_kovo.py)이 채운다.
모든 행은 KOVO 코드를 유일한 자연 키로 갖고, Spark는 그 키로 upsert 한다(DB id는 내부용).
행을 ORM이 아니라 SQL로 쓰기 때문에 생성·수정 시각은 DB 기본값(Now())으로 채운다.
스키마(테이블 구조)는 Django 마이그레이션이 관리한다: 컬럼을 바꾸려면 여기 모델을 고치고 makemigrations.
"""
from django.db import models
from django.db.models.functions import Now


class Gender(models.TextChoices):
    """성별(리그 구분). DB에는 M/F, 화면에는 남자부/여자부."""
    MEN = "M", "남자부"
    WOMEN = "F", "여자부"


class PipelineModel(models.Model):
    """데이터 파이프라인이 쓰는 테이블의 공통 부모(추상 모델). `updated_at`은 병합 SQL이 upsert 할 때마다 now()로 바꾼다."""

    created_at = models.DateTimeField(db_default=Now(), editable=False)
    updated_at = models.DateTimeField(db_default=Now(), editable=False)

    class Meta:
        abstract = True


class Season(PipelineModel):
    """시즌 하나(예: 022 = 2025-26). 목록은 최신 시즌부터."""
    code = models.CharField(max_length=3, unique=True, help_text="KOVO season code, e.g. 022")
    name = models.CharField(max_length=100, help_text="e.g. 진에어 2025~2026 V-리그")
    years = models.CharField(max_length=9, help_text="e.g. 2025-2026")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "season"
        ordering = ["-code"]

    def __str__(self):
        return self.name


class Competition(PipelineModel):
    """한 시즌 안의 대회(리그) 하나: 정규리그, 포스트시즌 각 라운드, 컵대회. 시즌·대회 코드·리그 코드로 유일."""

    class Type(models.TextChoices):
        """대회 종류."""
        REGULAR = "REGULAR", "정규리그"
        POST = "POST", "포스트시즌"
        CUP = "CUP", "컵대회"

    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="competitions")
    gcode = models.CharField(max_length=3, help_text="001 V-League, 002 KOVO Cup")
    league_code = models.CharField(max_length=3, help_text="201 regular, 204 semi-PO, 202 PO, 203 final")
    type = models.CharField(max_length=10, choices=Type.choices)
    name = models.CharField(max_length=50)

    class Meta:
        db_table = "competition"
        constraints = [models.UniqueConstraint(fields=["season", "gcode", "league_code"], name="uq_competition")]

    def __str__(self):
        return f"{self.season.code} {self.name}"


class Team(PipelineModel):
    """팀. 이름은 후원사에 따라 바뀌므로 가장 최근 경기 기준 이름을 저장한다."""
    code = models.CharField(max_length=10, unique=True, help_text="KOVO team code, e.g. 1006")
    name = models.CharField(max_length=50, help_text="short name, e.g. 한국전력")
    full_name = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    home_city = models.CharField(max_length=30, blank=True)
    home_arena = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True, db_default=True, help_text="played in the latest collected season")
    # 이름은 후원사에 따라 바뀐다. 옛 시즌을 다시 처리(백필)할 때 최신 값을 덮어쓰면 안 되므로,
    # 병합 SQL은 들어오는 데이터가 이 날짜 이상으로 최신일 때만 행을 갱신한다.
    last_seen_on = models.DateField(null=True, blank=True, help_text="date of the latest match this row came from")

    class Meta:
        db_table = "team"
        ordering = ["gender", "name"]

    def __str__(self):
        return self.name


class Player(PipelineModel):
    """선수. 포지션은 가장 최근 박스스코어 기준(OH 아웃사이드 히터, OP 아포짓, MB 미들 블로커, S 세터, L 리베로)."""
    code = models.CharField(max_length=10, unique=True, help_text="KOVO player code, e.g. 1000525")
    name = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    position = models.CharField(max_length=2, blank=True, help_text="OH, OP, MB, S, L")
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    # 경기 API에는 없는 값들. 나중에 선수 API에서 채울 예정(COL-01). 관리자 보정으로도 채울 수 있다.
    birth_date = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, blank=True, help_text="ACTIVE, MILITARY, RETIRED")
    last_seen_on = models.DateField(null=True, blank=True, help_text="date of the latest match this row came from")

    class Meta:
        db_table = "player"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlayerHonor(PipelineModel):
    """선수의 수상·주요 기록 한 줄(COL-04). KOVO 선수 화면의 "수상 내역" 그대로이며 spark_jobs/curate_honors.py가
    전체를 교체한다. 원문 예: "2025~2026 V-리그 라운드 MVP (5R)" → season_years "2025-2026", title "라운드 MVP (5R)"."""

    class Kind(models.TextChoices):
        AWARD = "AWARD", "수상"                  # 정규리그·챔피언결정전 MVP, 신인선수상, 부문상, 라운드·월간 MVP 등
        TRIPLE_CROWN = "TRIPLE_CROWN", "트리플크라운"  # 한 경기 후위 공격·서브·블로킹 각 3개 이상
        RECORD = "RECORD", "기록 달성"            # 통산 기준 기록 달성(예: 득점 3000개 달성 10호)

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="honors")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    seq = models.PositiveSmallIntegerField(help_text="order in KOVO's list (oldest first)")
    season_years = models.CharField(max_length=9, blank=True,
                                    help_text="e.g. 2025-2026, or 2014 for a one-year cup; blank if the line has none")
    title = models.CharField(max_length=200, help_text="the line without its season prefix")
    text = models.CharField(max_length=300, help_text="KOVO's original line")

    class Meta:
        db_table = "player_honor"
        constraints = [models.UniqueConstraint(fields=["player", "kind", "seq"], name="uq_player_honor")]
        ordering = ["player", "kind", "seq"]
