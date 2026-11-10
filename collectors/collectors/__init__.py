"""수집기(collectors) 패키지. 외부 데이터(KOVO 기록, 뉴스 RSS/API, 실시간 문자중계)를 받아 Kafka로 보낸다.

모듈 구성은 설계서 4.7의 클래스 다이어그램을 따른다.
  RateLimiter(요청 간격 제한) → KovoClient(KOVO API 호출) → BaseCollector와 하위 수집기(무엇을 수집할지)
  → KafkaPublisher(Kafka 발행), CollectionLogger(수집 실행 기록)
명령줄 진입점은 __main__.py(`python -m collectors ...`)이다.
"""
