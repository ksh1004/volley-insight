"""모든 수집기가 함께 쓰는 실행 설정. 값은 .env 파일이나 컨테이너 환경 변수에서 읽는다(비밀 값은 코드에 두지 않음)."""
import os

# Kafka 접속 주소. 로컬 PC에서 실행할 때는 docker compose가 열어 둔 29092 포트를 쓴다
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
# KOVO 공식 사이트가 내부적으로 쓰는 공개 JSON API 주소
KOVO_API_BASE = os.environ.get("KOVO_API_BASE", "https://user-api.kovo.co.kr")
# NFR-01: KOVO에는 1초에 최대 1번만 요청한다
KOVO_MIN_INTERVAL_SEC = float(os.environ.get("KOVO_MIN_INTERVAL_SEC", "1.0"))
# 이 컴퓨터의 모든 수집 프로세스가 위 간격을 함께 지키게 하는 잠금 파일 경로(컨테이너에서 설정)
KOVO_RATE_LOCK_FILE = os.environ.get("KOVO_RATE_LOCK_FILE", "")
# 네이버 뉴스 검색 API 키(없으면 RSS만으로 뉴스를 모은다)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
