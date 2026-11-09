"""Analysis marts (design.md ERD 2.2), written by spark_jobs/build_marts.py.

TeamRating          Elo after every match (ANL-01)
TeamSeasonSummary   team record + indicators per competition, incl. side-out / break rates (ANL-02, ANL-11)
PlayerSeasonSummary player indicators per competition + percentile within position (ANL-05)
Predictions (MatchPrediction, ModelVersion) come with the prediction model (ANL-03, ANL-09).
PlayerType, PlayerStyle: player groups (K-means) and similar players, from spark_jobs/player_types.py (ANL-06).
"""
from django.conf import settings
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
    # play-by-play situations (ANL-11), from 2023-24 on
    attack_after_good_receive = models.DecimalField(max_digits=5, decimal_places=2, null=True,
                                                    help_text="% of first attacks killed after an excellent reception")
    attack_after_poor_receive = models.DecimalField(max_digits=5, decimal_places=2, null=True,
                                                    help_text="% of first attacks killed after any other reception")
    clutch_rallies = models.PositiveIntegerField(null=True, help_text="rallies started at 20:20 or later (10:10 in set 5)")
    clutch_win_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, help_text="% of those rallies won")

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


class ModelVersion(PipelineModel):
    """A registered MLflow model version (requirements 2.6), mirrored when it is used for predictions."""

    name = models.CharField(max_length=50, help_text="MLflow registered model, e.g. match-winner")
    version = models.PositiveIntegerField()
    mlflow_run_id = models.CharField(max_length=64)
    alias = models.CharField(max_length=20, blank=True, help_text="champion, candidate or empty")
    metrics = models.JSONField(default=dict, help_text="test metrics, incl. the Elo baseline on the same season")
    train_seasons = models.CharField(max_length=20, blank=True)
    test_season = models.CharField(max_length=3, blank=True)

    class Meta:
        db_table = "model_version"
        constraints = [models.UniqueConstraint(fields=["name", "version"], name="uq_model_version")]

    def __str__(self):
        return f"{self.name} v{self.version}"


class MatchPrediction(PipelineModel):
    """Pre-match prediction (ANL-03). Written only while the match has not started; is_correct is filled
    after the result is known (ANL-10), so the hit rate shown on the site is never a back-fitted one."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="predictions")
    model_version = models.ForeignKey(ModelVersion, on_delete=models.PROTECT, related_name="predictions")
    home_win_prob = models.DecimalField(max_digits=5, decimal_places=4)
    set_score_probs = models.JSONField(help_text='{"3:0": p, "3:1": p, ...} from the home side')
    pred_home_sets = models.PositiveSmallIntegerField()
    pred_away_sets = models.PositiveSmallIntegerField()
    predicted_at = models.DateTimeField()
    is_correct = models.BooleanField(null=True, help_text="winner predicted correctly; null until the match is over")

    class Meta:
        db_table = "match_prediction"
        constraints = [models.UniqueConstraint(fields=["match", "model_version"], name="uq_match_prediction")]
        indexes = [models.Index(fields=["match", "-predicted_at"], name="ix_match_prediction_latest")]


class PlayerType(PipelineModel):
    """Playing-style group from K-means on per-set records (ANL-06), one set of types per gender.

    Written by spark_jobs/player_types.py; every run replaces all rows (cluster numbers are not stable between runs).
    """

    gender = models.CharField(max_length=1)
    cluster_no = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=40, help_text="e.g. 아웃사이드 히터 · 리시브 안정")
    position = models.CharField(max_length=2, blank=True, help_text="most common position in the group")
    traits = models.JSONField(default=list, help_text="[{feature, label, z}] that set the group apart, strongest first")
    centroid = models.JSONField(default=dict, help_text="{feature: z-score} of the group centre")
    size = models.PositiveIntegerField()
    k = models.PositiveSmallIntegerField(help_text="number of groups chosen for this gender")
    silhouette = models.FloatField()
    mlflow_run_id = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "player_type"
        constraints = [models.UniqueConstraint(fields=["gender", "cluster_no"], name="uq_player_type")]


class PlayerStyle(PipelineModel):
    """A player's season record as a style: group, per-set features and the most similar players (ANL-06, WEB-07)."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="styles")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    type = models.ForeignKey(PlayerType, on_delete=models.CASCADE, related_name="players")
    sets = models.PositiveSmallIntegerField()
    provisional = models.BooleanField(help_text="few sets played yet: the group may still change")
    features = models.JSONField(help_text="{feature: value per set or rate}")
    z = models.JSONField(help_text="{feature: z-score among players of the same gender}")
    similar_season = models.JSONField(default=list, help_text="nearest players of the same season")
    similar_all = models.JSONField(default=list, help_text="nearest seasons of other players, any season")

    class Meta:
        db_table = "player_style"
        constraints = [models.UniqueConstraint(fields=["player", "season"], name="uq_player_style")]


class PlayerRating(PipelineModel):
    """Position rating of a player-season for the player card: each ability and an overall score, 1-99 where 60 is
    the average qualified player of the same gender and position (72 = one standard deviation better).
    Written by spark_jobs/player_ratings.py; the abilities and why they were chosen are described there."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="ratings")
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="+")
    gender = models.CharField(max_length=1)
    position = models.CharField(max_length=2)
    sets = models.PositiveSmallIntegerField()
    qualified = models.BooleanField(help_text="played at least a third of the team's sets (in the reference)")
    provisional = models.BooleanField(help_text="not qualified or under 30 sets: scores pulled toward the average")
    overall = models.PositiveSmallIntegerField(null=True, help_text="weighted abilities, 1-99")
    abilities = models.JSONField(default=list, help_text="[{key, label, weight, score, z, stats: [{metric, label, "
                                                         "unit, value}], note}] in the position's order")
    extra = models.JSONField(default=dict, help_text="numbers shown beside the card, e.g. 세트당 세트 성공 for setters")

    class Meta:
        db_table = "player_rating"
        constraints = [models.UniqueConstraint(fields=["player", "season"], name="uq_player_rating")]
        indexes = [models.Index(fields=["season", "gender", "position"], name="ix_player_rating_group")]


class PlayerCareerRating(PipelineModel):
    """Career card of a player at one position: each ability is his season scores averaged with the sets played as
    weights (each season was scored against its own era), the record behind it pooled over his seasons, and the
    overall the weighted average of those abilities. Written by spark_jobs/player_ratings.py with PlayerRating."""

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="career_ratings")
    gender = models.CharField(max_length=1)
    position = models.CharField(max_length=2)
    seasons = models.PositiveSmallIntegerField(help_text="rated seasons at this position")
    qualified_seasons = models.PositiveSmallIntegerField(help_text="seasons with at least a third of the team's sets")
    sets = models.PositiveIntegerField()
    first_season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    last_season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+")
    best_season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="+", null=True,
                                    help_text="highest overall among the qualified seasons (any season if none)")
    best_overall = models.PositiveSmallIntegerField(null=True)
    qualified = models.BooleanField(help_text="3+ qualified seasons: ranked all-time")
    overall = models.PositiveSmallIntegerField(null=True, help_text="weighted abilities, 1-99")
    abilities = models.JSONField(default=list, help_text="same shape as PlayerRating.abilities; stats may carry a "
                                                         "period when pooled over part of the career")
    extra = models.JSONField(default=dict)

    class Meta:
        db_table = "player_rating_career"
        constraints = [models.UniqueConstraint(fields=["player", "position"], name="uq_player_rating_career")]
        indexes = [models.Index(fields=["gender", "position"], name="ix_player_rating_career_group")]


class PredictionBacktest(PipelineModel):
    """Walk-forward test of a model version (ANL-10): each season predicted by a model trained only on the seasons
    before it, with the pre-match Elo probability of the same match as the baseline. Written by train_model.py."""

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="backtests")
    model_version = models.ForeignKey(ModelVersion, on_delete=models.CASCADE, related_name="backtests")
    home_win_prob = models.DecimalField(max_digits=5, decimal_places=4)
    elo_home_win_prob = models.DecimalField(max_digits=5, decimal_places=4)
    pred_home_sets = models.PositiveSmallIntegerField()
    pred_away_sets = models.PositiveSmallIntegerField()
    home_won = models.BooleanField()
    train_seasons = models.CharField(max_length=10, help_text="seasons the model saw, e.g. 001-021")

    class Meta:
        db_table = "prediction_backtest"
        constraints = [models.UniqueConstraint(fields=["match", "model_version"], name="uq_prediction_backtest")]


class ModelChange(models.Model):
    """ADM-05: an admin moved the `champion` alias (the serving model) in the MLflow registry. Written by the admin
    page, so the reason and the person stay on record next to MLflow's own history."""

    model_name = models.CharField("모델", max_length=50)
    from_version = models.PositiveIntegerField("이전 버전", null=True, blank=True)
    to_version = models.PositiveIntegerField("새 버전")
    reason = models.TextField("사유")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+", verbose_name="바꾼 사람")
    changed_at = models.DateTimeField("바꾼 시각", auto_now_add=True)
    predict_run = models.CharField("예측 재실행", max_length=100, blank=True, help_text="Airflow ml_daily_predict run id")

    class Meta:
        db_table = "model_change"
        ordering = ["-changed_at"]
        verbose_name = verbose_name_plural = "모델 관리"

    def __str__(self):
        return f"{self.model_name} v{self.from_version} → v{self.to_version}"
