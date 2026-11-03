"""Analysis marts (design.md ERD 2.2), written by spark_jobs/build_marts.py.

TeamRating          Elo after every match (ANL-01)
TeamSeasonSummary   team record + indicators per competition, incl. side-out / break rates (ANL-02, ANL-11)
PlayerSeasonSummary player indicators per competition + percentile within position (ANL-05)
Predictions (MatchPrediction, ModelVersion) come with the prediction model (ANL-03, ANL-09).
"""
from django.db import models

from league.models import Competition, PipelineModel, Player, Season, Team
from matches.models import Match


class TeamRating(PipelineModel):
    """Team Elo after one match. The first row of a season starts from the previous season's rating,
    pulled a third of the way back to 1500 (rosters change between seasons)."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="ratings")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="ratings")
    rated_on = models.DateField()
    rating = models.DecimalField(max_digits=7, decimal_places=2)
    rating_change = models.DecimalField(max_digits=6, decimal_places=2)
    win_prob = models.DecimalField(max_digits=5, decimal_places=4, help_text="Elo-expected win probability before the match")

    class Meta:
        db_table = "team_rating"
        ordering = ["team", "rated_on", "match"]
        constraints = [models.UniqueConstraint(fields=["team", "match"], name="uq_team_rating")]
        indexes = [models.Index(fields=["team", "rated_on"], name="ix_team_rating_team")]


class Rates(models.Model):
    """Indicators shared by team and player season summaries (null when there were no attempts)."""

    attack_success_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    attack_efficiency = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    receive_efficiency = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    block_per_set = models.DecimalField(max_digits=5, decimal_places=3, null=True)
    serve_per_set = models.DecimalField(max_digits=5, decimal_places=3, null=True)
    dig_per_set = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    set_per_set = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    error_per_set = models.DecimalField(max_digits=5, decimal_places=3, null=True)

    class Meta:
        abstract = True


class TeamSeasonSummary(PipelineModel, Rates):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="season_summaries")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="team_summaries")
    rank = models.PositiveSmallIntegerField(null=True)
    matches = models.PositiveSmallIntegerField()
    wins = models.PositiveSmallIntegerField()
    losses = models.PositiveSmallIntegerField()
    win_points = models.PositiveSmallIntegerField(help_text="승점")
    sets_won = models.PositiveSmallIntegerField()
    sets_lost = models.PositiveSmallIntegerField()
    points_won = models.PositiveIntegerField()
    points_lost = models.PositiveIntegerField()
    sideout_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, help_text="% of receiving rallies won")
    break_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, help_text="% of serving rallies won")
    final_rating = models.DecimalField(max_digits=7, decimal_places=2, null=True, help_text="Elo after its last match")

    class Meta:
        db_table = "team_season_summary"
        constraints = [models.UniqueConstraint(fields=["team", "competition"], name="uq_team_season_summary")]
        indexes = [models.Index(fields=["competition", "rank"], name="ix_team_summary_rank")]


class PlayerSeasonSummary(PipelineModel, Rates):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="season_summaries")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="player_summaries")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    position = models.CharField(max_length=2, blank=True)
    matches = models.PositiveSmallIntegerField()
    sets = models.PositiveSmallIntegerField()
    points = models.PositiveSmallIntegerField()
    qualified = models.BooleanField(help_text="played at least a third of the team's sets (ranking rule)")
    percentiles = models.JSONField(default=dict, help_text="indicator -> percentile among qualified players "
                                                           "of the same position and gender, 0-100")

    class Meta:
        db_table = "player_season_summary"
        constraints = [models.UniqueConstraint(fields=["player", "competition", "team"], name="uq_player_season_summary")]
        indexes = [models.Index(fields=["competition"], name="ix_player_summary_comp")]
