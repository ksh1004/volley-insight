from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Service user (design.md ERD 2.3). Signs up with email + password; `username` holds the same email."""

    email = models.EmailField("이메일", unique=True)
    nickname = models.CharField("닉네임", max_length=30, blank=True)

    class Meta:
        db_table = "user"


class FavoriteTeam(models.Model):
    """Team the user follows (WEB-12): shown first on the home page."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorite_teams")
    team = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "favorite_team"
        constraints = [models.UniqueConstraint(fields=["user", "team"], name="uq_favorite_team")]


class FavoritePlayer(models.Model):
    """Player the user follows (WEB-12)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorite_players")
    player = models.ForeignKey("league.Player", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "favorite_player"
        constraints = [models.UniqueConstraint(fields=["user", "player"], name="uq_favorite_player")]
