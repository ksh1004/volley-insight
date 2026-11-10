"""Django 앱 설정: 시즌·대회·팀·선수와 순위."""
from django.apps import AppConfig


class LeagueConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'league'
