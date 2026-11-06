"""Volleyball article metadata and team/player tags (design.md ERD 2.3).

Rows are written by the Flink `article-enricher` job (flink_jobs/article_enricher.py), not by the web app:
it removes duplicates, tags teams and players from the title and summary, then upserts here by URL and
indexes the same document in Elasticsearch `articles`. Only title, summary, publisher and link are stored;
the article itself stays on the publisher's site (NFR-02, NFR-06).
"""
from django.db import models

from league.models import PipelineModel, Player, Team


class Article(PipelineModel):
    class Source(models.TextChoices):
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
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)

    class Meta:
        db_table = "article_team"
        constraints = [models.UniqueConstraint(fields=["article", "team"], name="uq_article_team")]
        indexes = [models.Index(fields=["team", "article"], name="ix_article_team_team")]


class ArticlePlayer(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    player = models.ForeignKey(Player, on_delete=models.CASCADE)

    class Meta:
        db_table = "article_player"
        constraints = [models.UniqueConstraint(fields=["article", "player"], name="uq_article_player")]
        indexes = [models.Index(fields=["player", "article"], name="ix_article_player_player")]
