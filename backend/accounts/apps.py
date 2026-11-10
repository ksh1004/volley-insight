"""Django 앱 설정: 회원(이메일 가입, 토큰 로그인, 관심 팀·선수)."""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
