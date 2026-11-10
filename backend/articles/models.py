"""배구 기사 메타데이터와 팀·선수 태그(설계서 ERD 2.3).

행은 웹 앱이 아니라 Flink `article-enricher` 작업(flink_jobs/article_enricher.py)이 쓴다: 중복을 없애고, 제목과
요약에서 팀·선수를 찾아 태그를 단 뒤, URL 기준으로 여기에 upsert 하고 같은 문서를 Elasticsearch `articles`에도
색인한다. 저장하는 것은 제목, 요약, 언론사, 링크뿐이고 기사 본문은 언론사 사이트에 그대로 둔다(NFR-02, NFR-06).
"""
from django.db import models

from league.models import PipelineModel, Player, Team


class Article(PipelineModel):
    """기사 하나. 같은 URL은 한 번만 저장된다(unique). 팀·선수 태그는 아래 연결 테이블로."""

    class Source(models.TextChoices):
        """어디서 모았는지."""
        RSS = "RSS", "RSS"
        NAVER_API = "NAVER_API", "네이버 뉴스 검색"

    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    publisher = models.CharField(max_length=50)
    published_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices)
    collected_at = models.DateTimeField(help_text="when the collector fetched it (envelope fetched_at)")
    teams = models.ManyToManyField(Team, through="ArticleTeam", related_name="articles")
    players = models.ManyToManyField(Player, through="ArticlePlayer", related_name="articles")

    class Meta:
        db_table = "article"
        ordering = ["-published_at"]
        indexes = [models.Index(fields=["-published_at"], name="ix_article_published")]

    def __str__(self):
        return self.title


class ArticleTeam(models.Model):
    """기사-팀 태그 연결(다대다). 팀별 기사 목록을 빨리 찾도록 (팀, 기사) 색인을 둔다."""
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)

    class Meta:
        db_table = "article_team"
        constraints = [models.UniqueConstraint(fields=["article", "team"], name="uq_article_team")]
        indexes = [models.Index(fields=["team", "article"], name="ix_article_team_team")]


class ArticlePlayer(models.Model):
    """기사-선수 태그 연결(다대다)."""
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    player = models.ForeignKey(Player, on_delete=models.CASCADE)

    class Meta:
        db_table = "article_player"
        constraints = [models.UniqueConstraint(fields=["article", "player"], name="uq_article_player")]
        indexes = [models.Index(fields=["player", "article"], name="ix_article_player_player")]
