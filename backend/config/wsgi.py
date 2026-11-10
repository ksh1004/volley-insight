"""WSGI 진입점: 일반(동기) 웹 서버가 불러 쓰는 `application` 객체를 만든다(REST API를 처리하는 django-api 서비스).

참고: https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
