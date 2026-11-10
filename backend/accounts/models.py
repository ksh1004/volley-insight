"""회원 관련 모델: 사용자, 관심 팀, 관심 선수(WEB-11, WEB-12)."""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """서비스 회원(설계서 ERD 2.3). 이메일 + 비밀번호로 가입하고, Django 기본 `username`에도 같은 이메일을 넣는다.
    개인정보는 이메일과 닉네임만 받는다(NFR-03)."""

    email = models.EmailField("이메일", unique=True)
    nickname = models.CharField("닉네임", max_length=30, blank=True)

    class Meta:
        db_table = "user"


class FavoriteTeam(models.Model):
    """회원이 관심 등록한 팀(WEB-12): 홈 화면 '내 관심'에 먼저 보인다. 한 사람이 같은 팀을 두 번 넣을 수 없다."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorite_teams")
    team = models.ForeignKey("league.Team", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "favorite_team"
        constraints = [models.UniqueConstraint(fields=["user", "team"], name="uq_favorite_team")]


class FavoritePlayer(models.Model):
    """회원이 관심 등록한 선수(WEB-12)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorite_players")
    player = models.ForeignKey("league.Player", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "favorite_player"
        constraints = [models.UniqueConstraint(fields=["user", "player"], name="uq_favorite_player")]
