"""Records each collection run (COL-08). Events go to the `ops.collection-log` topic (design.md 1.7)."""
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)
TOPIC = "ops.collection-log"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CollectionLogger:
    def __init__(self, publisher):
        self.publisher = publisher

    def start(self, job: str, target: str, run_id: str) -> dict:
        record = {"run_id": run_id, "job_name": job, "target": target, "status": "RUNNING", "started_at": _now()}
        self.publisher.publish(TOPIC, job, record)
        log.info("start %s (%s)", job, target)
        return record

    def finish(self, record: dict, status: str, count: int, error: str | None = None) -> dict:
        done = {**record, "status": status, "record_count": count, "error_message": error, "finished_at": _now()}
        self.publisher.publish(TOPIC, record["job_name"], done)
        log.info("finish %s %s count=%d%s", record["job_name"], status, count, f" error={error}" if error else "")
        return done
