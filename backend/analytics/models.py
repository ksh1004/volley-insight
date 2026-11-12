"""분석 결과 테이블들(설계서 ERD 2.2). 웹 앱은 읽기만 하고, 각 테이블은 아래 Spark 작업이 채운다.

TeamRating          경기마다의 팀 Elo (ANL-01)                                   ← build_marts.py
TeamSeasonSummary   대회별 팀 성적 + 지표, 사이드아웃/브레이크율 포함 (ANL-02, ANL-11) ← build_marts.py
PlayerSeasonSummary 대회별 선수 지표 + 같은 포지션 안 백분위 (ANL-05)            ← build_marts.py
ModelVersion, MatchPrediction  예측 모델 버전과 경기 전 예측 (ANL-03, ANL-09)      ← predict_matches.py
PredictionBacktest  모델 버전의 워크포워드 백테스트 (ANL-10)                     ← train_model.py
PlayerType, PlayerStyle  선수 유형(K-means 그룹)과 비슷한 선수 (ANL-06)          ← player_types.py
PlayerRating, PlayerCareerRating  포지션별 능력치 카드(시즌, 통산)               ← player_ratings.py
ModelChange         관리자가 서비스 모델을 바꾼 기록 (ADM-05)                     ← Django 관리자 화면
"""
from django.conf import settings
from django.db import models

from league.models import Competition, PipelineModel, Player, Season, Team
from matches.models import Match


class TeamRating(PipelineModel):
    """경기 하나를 마친 뒤의 팀 Elo. 시즌 첫 행은 지난 시즌 점수를 1500 쪽으로 절반 되돌린 값에서 시작한다
    (시즌 사이에 선수단이 바뀌므로)."""

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
    """팀·선수 시즌 요약이 함께 쓰는 비율 지표(추상 모델). 시도가 없었으면 NULL."""

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
    """팀 × 대회 하나의 요약: 승패·승점·세트·점수, 순위(정규리그), 지표, 마지막 Elo, 상황별 효율."""

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
    # 문자중계로 계산한 상황별 효율(ANL-11), 2023-24부터
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
    """MLflow에 등록된 모델 버전(요구사항 2.6). 예측에 쓰일 때 서비스 DB에도 옮겨 적는다(화면이 MLflow 없이 읽도록)."""

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
    """경기 전 예측(ANL-03). 경기가 시작하기 전까지만 쓰이고, 결과가 나오면 is_correct가 채워진다(ANL-10).
    그래서 사이트의 적중률은 결과를 보고 끼워 맞춘 값이 절대 아니다."""

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
    """세트당 기록을 K-means로 나눈 플레이 스타일 그룹(ANL-06). 성별마다 유형 묶음이 따로 있다.

    spark_jobs/player_types.py가 쓰고, 실행마다 모든 행을 바꾼다(그룹 번호가 실행마다 같다는 보장이 없어서).
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
    """선수-시즌 하나의 스타일: 속한 그룹, 세트당 특징 값과 z, 가장 비슷한 선수들(ANL-06, WEB-07)."""

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
    """선수 카드의 시즌 능력치: 능력치별 점수와 종합 점수(1~99). 60 = 같은 성별·포지션 순위 자격 선수의 평균,
    72 = 표준편차 1만큼 위. spark_jobs/player_ratings.py가 쓰고, 능력치 구성과 그 이유는 그 파일 머리말에 있다."""

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
    """한 포지션에서의 통산 카드: 능력치마다 시즌 점수를 출전 세트로 가중 평균(각 시즌이 그 시기 기준으로 매겨져
    있어 시대가 달라도 같은 눈금), 근거 기록은 시즌 기록을 모두 합친 값, 종합은 그 능력치들의 가중 평균.
    spark_jobs/player_ratings.py가 PlayerRating과 함께 쓴다."""

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
    """모델 버전 하나의 워크포워드 시험(ANL-10): 각 시즌을 그 이전 시즌들로만 학습한 모델로 예측한 결과와,
    비교 기준인 같은 경기의 경기 전 Elo 확률. train_model.py가 쓴다."""

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
    """ADM-05: 관리자가 MLflow의 `champion` 별칭(서비스 모델)을 옮긴 기록. 관리자 화면이 쓰며,
    MLflow 자체 이력 옆에 '누가, 왜' 바꿨는지를 남긴다."""

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
