from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Service user (design.md ERD 2.3). Defined before the first migration so it can change freely later."""

    email = models.EmailField("이메일", unique=True)
    nickname = models.CharField("닉네임", max_length=30, blank=True)

    class Meta:
        db_table = "user"
