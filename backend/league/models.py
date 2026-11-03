"""Seasons, competitions, teams and players (design.md ERD 2.1).

These tables are filled by the Spark curation job (spark_jobs/curate_kovo.py), not by the web app.
Every row keeps the KOVO code as a unique natural key; Spark upserts by that key.
Timestamps use database defaults because rows are written by SQL, not through the ORM.
"""
from django.db import models
from django.db.models.functions import Now


class Gender(models.TextChoices):
    MEN = "M", "남자부"
    WOMEN = "F", "여자부"


class PipelineModel(models.Model):
    """Rows written by the data pipeline. `updated_at` is set by the merge SQL on every upsert."""

    created_at = models.DateTimeField(db_default=Now(), editable=False)
    updated_at = models.DateTimeField(db_default=Now(), editable=False)

    class Meta:
        abstract = True


class Season(PipelineModel):
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
    class Type(models.TextChoices):
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
    code = models.CharField(max_length=10, unique=True, help_text="KOVO team code, e.g. 1006")
    name = models.CharField(max_length=50, help_text="short name, e.g. 한국전력")
    full_name = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    home_city = models.CharField(max_length=30, blank=True)
    home_arena = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True, db_default=True, help_text="played in the latest collected season")
    # Names change with sponsors. A backfill of an old season must not overwrite newer values,
    # so the merge only updates a row when the incoming data is at least this recent.
    last_seen_on = models.DateField(null=True, blank=True, help_text="date of the latest match this row came from")

    class Meta:
        db_table = "team"
        ordering = ["gender", "name"]

    def __str__(self):
        return self.name


class Player(PipelineModel):
    code = models.CharField(max_length=10, unique=True, help_text="KOVO player code, e.g. 1000525")
    name = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    position = models.CharField(max_length=2, blank=True, help_text="OH, OP, MB, S, L")
    height_cm = models.PositiveSmallIntegerField(null=True, blank=True)
    # Not in the match APIs; filled later from the player API (COL-01).
    birth_date = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, blank=True, help_text="ACTIVE, MILITARY, RETIRED")
    last_seen_on = models.DateField(null=True, blank=True, help_text="date of the latest match this row came from")

    class Meta:
        db_table = "player"
        ordering = ["name"]

    def __str__(self):
        return self.name
