"""ASGI 진입점: 비동기 서버(Uvicorn)가 불러 쓰는 `application` 객체를 만든다.

실시간 SSE 스트림을 처리하는 django-sse 서비스가 이것으로 Django를 띄운다(오래 열려 있는 연결이 많아 비동기가 필요).
참고: https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_asgi_application()
