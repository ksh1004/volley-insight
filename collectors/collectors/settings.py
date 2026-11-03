"""Runtime settings shared by every collector (values come from .env / container environment)."""
import os

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
KOVO_API_BASE = os.environ.get("KOVO_API_BASE", "https://user-api.kovo.co.kr")
# NFR-01: at most one request per second to KOVO
KOVO_MIN_INTERVAL_SEC = float(os.environ.get("KOVO_MIN_INTERVAL_SEC", "1.0"))
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
