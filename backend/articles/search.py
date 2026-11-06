"""Elasticsearch queries for the search page and the header autocomplete (WEB-10, design.md 2.5).

Indexes (both are aliases that the pipeline moves when it rebuilds):
  articles  written by Flink article-enricher   title/summary with nori (Korean words) + 2-letter n-grams
  entities  written by Spark index_entities.py   teams and players; `suggest` is search_as_you_type
Django only reads them (design.md 1.1: the service never computes or writes search data).
"""
from functools import lru_cache

from django.conf import settings

ARTICLES, ENTITIES = "articles", "entities"
ENTITY_FIELDS = ["type", "code", "name", "team_code", "team_name", "gender", "position", "active"]
ARTICLE_FIELDS = ["url", "title", "summary", "publisher", "source", "published_at", "team_codes", "team_names",
                  "player_codes", "player_names"]


class SearchUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def client():
    from elasticsearch import Elasticsearch

    return Elasticsearch(settings.ELASTICSEARCH_URL, request_timeout=3, max_retries=1)


def _search(index, body):
    from elasticsearch import ApiError, TransportError

    try:
        return client().search(index=index, **body)
    except (ApiError, TransportError) as exc:
        raise SearchUnavailable(str(exc)) from exc


def entity_query(q):
    """Prefix match on each word (김연 -> 김연경) or any two letters inside the name (기업은행 -> IBK기업은행),
    ranked by `weight` (current teams, then players with many recent games)."""
    return {"function_score": {
        "query": {"bool": {"should": [
            {"multi_match": {"query": q, "type": "bool_prefix", "fields": ["suggest", "suggest._2gram", "suggest._3gram"]}},
            {"match": {"suggest_bigram": {"query": q, "operator": "and"}}},
        ], "minimum_should_match": 1}},
        "field_value_factor": {"field": "weight", "modifier": "log1p", "missing": 0},
        "boost_mode": "multiply",
    }}


def suggest(q, size=8):
    res = _search(ENTITIES, {"query": entity_query(q), "size": size, "_source": ENTITY_FIELDS})
    return [h["_source"] for h in res["hits"]["hits"]]


def search_entities(q, size=10):
    out = {"team": [], "player": []}
    for doc in suggest(q, size * 2):
        if len(out[doc["type"]]) < size:
            out[doc["type"]].append(doc)
    return out


def search_articles(q, page=1, size=10):
    """Words (nori) count most, two-letter pieces catch names nori splits; newer articles rank a little higher."""
    text = {"bool": {"should": [
        {"match": {"title": {"query": q, "boost": 3}}},
        {"match": {"title.bigram": {"query": q, "minimum_should_match": "75%", "boost": 2}}},
        {"match": {"summary": {"query": q}}},
        {"match": {"summary.bigram": {"query": q, "minimum_should_match": "75%", "boost": 0.5}}},
        {"term": {"team_names": {"value": q, "boost": 3}}},
        {"term": {"player_names": {"value": q, "boost": 3}}},
    ], "minimum_should_match": 1}}
    body = {
        "query": {"function_score": {"query": text, "functions": [
            {"gauss": {"published_at": {"scale": "30d", "decay": 0.5}}, "weight": 1}], "boost_mode": "sum"}},
        "from": (page - 1) * size, "size": size, "_source": ARTICLE_FIELDS, "track_total_hits": True,
        # encoder=html escapes the article text, so only our <mark> tags are HTML
        "highlight": {"encoder": "html", "pre_tags": ["<mark>"], "post_tags": ["</mark>"],
                      "fields": {"title": {"number_of_fragments": 0}, "summary": {"number_of_fragments": 0},
                                 "title.bigram": {"number_of_fragments": 0}, "summary.bigram": {"number_of_fragments": 0}}},
    }
    res = _search(ARTICLES, body)
    results = []
    for h in res["hits"]["hits"]:
        hl = h.get("highlight", {})
        doc = h["_source"]
        # same shape as /api/articles
        doc["teams"] = [{"code": c, "name": n} for c, n in zip(doc.pop("team_codes", []), doc.pop("team_names", []))]
        doc["players"] = [{"code": c, "name": n} for c, n in zip(doc.pop("player_codes", []), doc.pop("player_names", []))]
        doc["highlight"] = {"title": (hl.get("title") or hl.get("title.bigram") or [None])[0],
                            "summary": (hl.get("summary") or hl.get("summary.bigram") or [None])[0]}
        results.append(doc)
    return {"total": res["hits"]["total"]["value"], "page": page, "results": results}
