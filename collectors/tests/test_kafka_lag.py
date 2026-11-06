from datetime import datetime, timezone

from collectors.kafka_lag import ship, to_docs


def test_docs_use_metricbeat_field_names():
    rows = [("article-enricher", "news.article", 0, 40, 47, 7)]
    d = next(to_docs(rows, now=datetime(2026, 11, 4, tzinfo=timezone.utc)))
    assert d["kafka"]["consumergroup"] == {"id": "article-enricher", "consumer_lag": 7, "offset": 40}
    assert d["kafka"]["topic"]["name"] == "news.article" and d["metricset"]["name"] == "consumergroup"


def test_ship_bulk_create():
    sent = {}

    class Http:
        def post(self, url, data, **kw):
            sent["url"], sent["data"] = url, data.decode()

            class R:
                def raise_for_status(self): pass
                def json(self): return {"errors": False}
            return R()

    assert ship(to_docs([("g", "t", 0, 1, 3, 2)]), http=Http()) == 1
    assert sent["url"].endswith("/metrics-volley/_bulk") and sent["data"].startswith('{"create": {}}')
