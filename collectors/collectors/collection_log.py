"""수집 실행 기록(COL-08). 실행마다 시작·종료 이벤트를 `ops.collection-log` 토픽으로 보낸다(설계서 1.7).

이 이벤트는 Filebeat/Elasticsearch를 거쳐 Kibana 운영 대시보드와 Django 관리자 '수집 현황' 화면에 쓰인다.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)
TOPIC = "ops.collection-log"


def _now():
    """지금 시각(UTC, 초 단위 ISO 문자열)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CollectionLogger:
    """수집 실행 하나의 시작(RUNNING)과 끝(SUCCESS/FAILED)을 Kafka에 기록한다."""

    def __init__(self, publisher):
        self.publisher = publisher

    def start(self, job: str, target: str, run_id: str) -> dict:
        """시작 이벤트를 보내고 그 레코드를 돌려준다. 끝날 때 finish()에 그대로 넘긴다."""
        record = {"run_id": run_id, "job_name": job, "target": target, "status": "RUNNING", "started_at": _now()}
        self.publisher.publish(TOPIC, job, record)
        log.info("start %s (%s)", job, target)
        return record

    def finish(self, record: dict, status: str, count: int, error: str | None = None) -> dict:
        """종료 이벤트를 보낸다. status는 SUCCESS/FAILED, count는 보낸 메시지 수, error는 실패 사유."""
        done = {**record, "status": status, "record_count": count, "error_message": error, "finished_at": _now()}
        self.publisher.publish(TOPIC, record["job_name"], done)
        log.info("finish %s %s count=%d%s", record["job_name"], status, count, f" error={error}" if error else "")
        return done
