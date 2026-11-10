"""수집한 레코드 하나를 Kafka에 보낼 때 쓰는 표준 메시지 형식(봉투, envelope).

원본 API 응답은 `payload`에 손대지 않고 그대로 담는다. 변환은 나중에 Spark/Flink가 한다(ELT 방식:
먼저 적재하고 나중에 변환). 봉투에는 "어디서, 언제, 어떤 요청으로" 가져왔는지도 함께 적어 두어,
어떤 레코드든 출처를 추적하거나 같은 요청으로 다시 받아 올 수 있다.
"""
from datetime import datetime, timezone


def make_envelope(*, source: str, endpoint: str, params: dict, payload, collector: str, run_id: str) -> dict:
    """봉투 딕셔너리를 만든다.

    source: 데이터 출처(예: "kovo", "news"), endpoint: 호출한 API 경로, params: 요청 파라미터,
    payload: API 응답 원본, collector: 수집기 이름, run_id: 이번 수집 실행의 ID(같은 실행의 메시지끼리 묶음).
    fetched_at은 UTC 기준 수집 시각(초 단위)이다.
    """
    return {
        "source": source,
        "endpoint": endpoint,
        "params": params,
        "collector": collector,
        "run_id": run_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "payload": payload,
    }
