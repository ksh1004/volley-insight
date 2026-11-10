"""Django 앱 설정: 데이터 운영(품질 검사 결과, 데이터 보정, 수집 현황, 수동 실행). 관리자 화면 전용."""
from django.apps import AppConfig


class OpsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ops'
    verbose_name = '데이터 운영'
