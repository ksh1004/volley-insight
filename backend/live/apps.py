"""Django 앱 설정: 실시간 경기 상태와 SSE 스트림."""
from django.apps import AppConfig


class LiveConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'live'
