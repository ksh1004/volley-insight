"""Django 앱 설정: 분석 결과(Elo, 시즌 요약, 예측, 선수 유형·능력치)."""
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analytics'
