#!/usr/bin/env python
"""Django 관리 명령 도구. 예: python manage.py migrate(DB 스키마 반영), test(테스트), createsuperuser(관리자 계정)."""
import os
import sys


def main():
    """설정 모듈을 지정하고 명령줄 인자대로 관리 명령을 실행한다."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
