"""Standard Kafka message body for every collected record.

The original API response is kept untouched in `payload` (ELT: transform later in Spark/Flink),
and the envelope records where and when it came from so any record can be re-fetched or traced.
"""
from datetime import datetime, timezone


def make_envelope(*, source: str, endpoint: str, params: dict, payload, collector: str, run_id: str) -> dict:
    return {
        "source": source,
        "endpoint": endpoint,
        "params": params,
        "collector": collector,
        "run_id": run_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "payload": payload,
    }
