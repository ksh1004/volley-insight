"""Django 앱 설정: 경기·세트·박스스코어·문자중계."""
from django.apps import AppConfig


class MatchesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'matches'
