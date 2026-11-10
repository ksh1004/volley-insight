"""검색 화면과 머리글 자동완성의 Elasticsearch 질의(WEB-10, 설계서 2.5).

색인(둘 다 별칭이라, 파이프라인이 새로 만들 때 별칭만 옮긴다):
  articles  Flink article-enricher가 씀   제목·요약을 nori(한국어 형태소 분석기)로 + 2글자 n-gram으로
  entities  Spark index_entities.py가 씀   팀과 선수. `suggest` 필드는 search_as_you_type(입력 중 검색) 형식
Django는 읽기만 한다(설계서 1.1: 서비스는 검색 데이터를 계산하거나 쓰지 않는다).

  n-gram: 글자를 2개씩 잘라 색인하는 방식. 형태소 분석기가 이름을 이상하게 나눠도 부분 일치로 찾을 수 있다.
"""
from functools import lru_cache

from django.conf import settings

ARTICLES, ENTITIES = "articles", "entities"
# 응답에 담을 필드(문서 전체를 가져오지 않음)
ENTITY_FIELDS = ["type", "code", "name", "team_code", "team_name", "gender", "position", "active"]
ARTICLE_FIELDS = ["url", "title", "summary", "publisher", "source", "published_at", "team_codes", "team_names",
                  "player_codes", "player_names"]


class SearchUnavailable(RuntimeError):
    """검색 엔진에 접속할 수 없거나 질의가 실패했을 때. 뷰가 받아 503으로 바꾼다."""


@lru_cache(maxsize=1)
def client():
    """Elasticsearch 클라이언트(프로세스당 하나를 캐시해 재사용). 응답이 3초 넘으면 포기하고 한 번만 재시도한다."""
    from elasticsearch import Elasticsearch

    return Elasticsearch(settings.ELASTICSEARCH_URL, request_timeout=3, max_retries=1)


def _search(index, body):
    """검색 하나를 실행한다. 연결·API 오류는 SearchUnavailable로 바꿔 던진다."""
    from elasticsearch import ApiError, TransportError

    try:
        return client().search(index=index, **body)
    except TransportError as exc:
        # 연결 오류 뒤에는 캐시된 클라이언트가 고장 난 채로 남을 수 있다(2026-11-09: Django가 Elasticsearch보다 먼저
        # 떠서 재시작 전까지 모든 검색이 시간 초과됨). 캐시를 버리면 다음 요청이 새 클라이언트를 만든다.
        client.cache_clear()
        raise SearchUnavailable(str(exc)) from exc
    except ApiError as exc:
        raise SearchUnavailable(str(exc)) from exc


def entity_query(q):
    """팀·선수 질의: 단어 앞부분 일치(김연 → 김연경) 또는 이름 안의 두 글자 일치(기업은행 → IBK기업은행).
    순서는 `weight`(활동 중인 팀 → 최근 경기가 많은 선수)를 곱해 정한다(log1p로 너무 큰 차이를 완화)."""
    return {"function_score": {
        "query": {"bool": {"should": [
            {"multi_match": {"query": q, "type": "bool_prefix", "fields": ["suggest", "suggest._2gram", "suggest._3gram"]}},
            {"match": {"suggest_bigram": {"query": q, "operator": "and"}}},
        ], "minimum_should_match": 1}},
        "field_value_factor": {"field": "weight", "modifier": "log1p", "missing": 0},
        "boost_mode": "multiply",
    }}


def suggest(q, size=8):
    """자동완성: 팀·선수 문서를 size개까지."""
    res = _search(ENTITIES, {"query": entity_query(q), "size": size, "_source": ENTITY_FIELDS})
    return [h["_source"] for h in res["hits"]["hits"]]


def search_entities(q, size=10):
    """검색 화면용: 팀과 선수를 나눠 각각 최대 size개(넉넉히 2배를 가져와 나눔)."""
    out = {"team": [], "player": []}
    for doc in suggest(q, size * 2):
        if len(out[doc["type"]]) < size:
            out[doc["type"]].append(doc)
    return out


def search_articles(q, page=1, size=10):
    """기사 검색: 형태소 단어 일치가 가장 중요하고, 두 글자 조각은 nori가 나눠 버린 이름을 잡는다.
    태그(팀·선수 이름)와 정확히 같으면 크게 가산, 최신 기사일수록 조금 가산(30일마다 절반으로 줄어드는 가우스 감쇠).
    결과는 /api/articles와 같은 모양에 강조(highlight) 문구를 더해 돌려준다."""
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
        # encoder=html: 기사 글자는 HTML 이스케이프하고 우리가 넣는 <mark> 태그만 HTML로 남긴다(XSS 방지)
        "highlight": {"encoder": "html", "pre_tags": ["<mark>"], "post_tags": ["</mark>"],
                      "fields": {"title": {"number_of_fragments": 0}, "summary": {"number_of_fragments": 0},
                                 "title.bigram": {"number_of_fragments": 0}, "summary.bigram": {"number_of_fragments": 0}}},
    }
    res = _search(ARTICLES, body)
    results = []
    for h in res["hits"]["hits"]:
        hl = h.get("highlight", {})
        doc = h["_source"]
        # /api/articles와 같은 모양으로 바꾼다
        doc["teams"] = [{"code": c, "name": n} for c, n in zip(doc.pop("team_codes", []), doc.pop("team_names", []))]
        doc["players"] = [{"code": c, "name": n} for c, n in zip(doc.pop("player_codes", []), doc.pop("player_names", []))]
        doc["highlight"] = {"title": (hl.get("title") or hl.get("title.bigram") or [None])[0],
                            "summary": (hl.get("summary") or hl.get("summary.bigram") or [None])[0]}
        results.append(doc)
    return {"total": res["hits"]["total"]["value"], "page": page, "results": results}
