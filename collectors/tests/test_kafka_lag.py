"""kafka-lag 서비스(collectors/kafka_lag.py) 테스트: Kafka 컨슈머 지연을 Metricbeat와 같은 모양의 문서로 바꿔
Elasticsearch metrics-volley 데이터 스트림에 넣는지 확인한다."""
from datetime import datetime, timezone

from collectors.kafka_lag import ship, to_docs


def test_docs_use_metricbeat_field_names():
    """(그룹, 토픽, 파티션, 커밋 위치, 끝 위치, 지연) 행이 Metricbeat kafka 모듈의 필드 이름으로 바뀐다."""
    rows = [("article-enricher", "news.article", 0, 40, 47, 7)]
    d = next(to_docs(rows, now=datetime(2026, 11, 4, tzinfo=timezone.utc)))
    assert d["kafka"]["consumergroup"] == {"id": "article-enricher", "consumer_lag": 7, "offset": 40}
    assert d["kafka"]["topic"]["name"] == "news.article" and d["metricset"]["name"] == "consumergroup"


def test_ship_bulk_create():
    """_bulk API로 보내며, 데이터 스트림이라 문서마다 {"create": {}} 줄을 앞에 붙인다."""
    sent = {}

    class Http:  # requests 흉내: 보낸 주소와 본문을 sent에 남긴다
        def post(self, url, data, **kw):
            sent["url"], sent["data"] = url, data.decode()

            class R:
                def raise_for_status(self): pass
                def json(self): return {"errors": False}
            return R()

    assert ship(to_docs([("g", "t", 0, 1, 3, 2)]), http=Http()) == 1
    assert sent["url"].endswith("/metrics-volley/_bulk") and sent["data"].startswith('{"create": {}}')
