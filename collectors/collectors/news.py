"""스포츠 RSS 피드와 네이버 뉴스 검색 API에서 배구 기사의 메타데이터를 모은다(COL-07).

저장하는 것은 메타데이터뿐이다: 제목, 짧은 요약, 언론사, 링크, 발행 시각(NFR-02, NFR-06 저작권 준수).
어떤 피드는 <description>에 기사 전문(과 이미지)을 통째로 넣기 때문에, Kafka로 보내기 *전에* 요약을
일반 텍스트 SUMMARY_CHARS 글자로 자른다. 기사 본문은 Kafka에도 HDFS에도 절대 들어가지 않는다.
스포츠 피드는 모든 종목을 다루므로, 배구가 아닌 기사는 여기서 걸러 낸다.

`news.article` 토픽의 메시지 하나가 기사 하나이고, 키는 기사 URL이다. 같은 기사가 실행마다, 출처마다
여러 번 오는 것은 정상이다(피드는 매시간 같은 최신 기사를 준다). 중복은 Flink `article-enricher`가 없앤다.

피드 선정(2026-11-04): 각 사이트 robots.txt가 허용하는 RSS 경로, 스포츠 섹션만.
네이버 API는 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정됐을 때만 쓴다(developers.naver.com에서 발급).
"""
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from . import settings
from .base import BaseCollector
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

USER_AGENT = "volley-insight-news/0.1 (non-commercial student project)"
SUMMARY_CHARS = 160  # 요약 최대 글자 수(본문을 옮겨 오지 않기 위한 제한)

# (피드 이름, 사이트에 보여 줄 언론사 이름, 피드 URL)
RSS_FEEDS = [
    ("yonhap", "연합뉴스", "https://www.yna.co.kr/rss/sports.xml"),
    ("newsis", "뉴시스", "https://www.newsis.com/RSS/sports.xml"),
    ("donga", "동아일보", "https://rss.donga.com/sports.xml"),
    ("hankyung", "한국경제", "https://www.hankyung.com/feed/sports"),
    ("hani", "한겨레", "https://www.hani.co.kr/rss/sports/"),
]
NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
NAVER_QUERIES = ["프로배구", "V리그 배구"]  # 네이버 검색어

# 네이버 API는 링크만 주므로 언론사 이름은 도메인으로 알아낸다(모르는 도메인은 도메인을 그대로 보여 줌)
PUBLISHERS = {
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "donga.com": "동아일보", "hankyung.com": "한국경제",
    "hani.co.kr": "한겨레", "news1.kr": "뉴스1", "osen.co.kr": "OSEN", "xportsnews.com": "엑스포츠뉴스",
    "sportsseoul.com": "스포츠서울", "sportschosun.com": "스포츠조선", "isplus.com": "일간스포츠",
    "mydaily.co.kr": "마이데일리", "spotvnews.co.kr": "스포티비뉴스", "sports.khan.co.kr": "스포츠경향",
    "sportsworldi.com": "스포츠월드", "joynews24.com": "조이뉴스24", "thespike.co.kr": "더스파이크",
    "stnsports.co.kr": "STN스포츠", "mk.co.kr": "매일경제", "chosun.com": "조선일보", "joongang.co.kr": "중앙일보",
    "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보", "kmib.co.kr": "국민일보", "seoul.co.kr": "서울신문",
    "segye.com": "세계일보", "munhwa.com": "문화일보", "edaily.co.kr": "이데일리", "mt.co.kr": "머니투데이",
    "newspim.com": "뉴스핌", "heraldcorp.com": "헤럴드경제", "fnnews.com": "파이낸셜뉴스", "ytn.co.kr": "YTN",
    "kbs.co.kr": "KBS", "imbc.com": "MBC", "sbs.co.kr": "SBS", "jtbc.co.kr": "JTBC", "mbn.co.kr": "MBN",
}

# 제목이나 요약에 이 단어 중 하나가 있으면 배구 기사로 본다. 제목에 '배구' 없이 팀 별칭만 쓰는 경우가 많아
# 팀 별칭도 넣었다.
VOLLEY_WORDS = ["배구", "V리그", "V-리그", "브이리그", "KOVO", "점보스", "스카이워커스", "블루팡스", "빅스톰",
                "읏맨", "힐스테이트", "핑크스파이더스", "알토스", "하이패스", "레드스파크스", "KIXX"]
# "배구조"는 배구가 아니다: 지배구조, 분배구조 ... 그래서 '배구' 뒤에 '조'가 오면 제외(부정 전방 탐색)
_VOLLEY = re.compile("|".join(re.escape(w) + ("(?!조)" if w == "배구" else "") for w in VOLLEY_WORDS),
                     re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")  # HTML 태그
_SPACE = re.compile(r"\s+")  # 연속 공백
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|from$)", re.IGNORECASE)  # URL의 광고·추적용 파라미터


def plain_text(value: str | None) -> str:
    """HTML(두 번 이스케이프된 경우 포함)을 한 줄짜리 일반 텍스트로 바꾼다."""
    text = html.unescape(value or "")
    text = html.unescape(_TAG.sub(" ", text))
    return _SPACE.sub(" ", _TAG.sub(" ", text)).strip()


def short(text: str, limit: int = SUMMARY_CHARS) -> str:
    """limit 글자보다 길면 잘라 '…'를 붙인다."""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def canonical_url(url: str) -> str:
    """같은 기사는 같은 URL이 되도록 정규화한다: https로 통일, # 뒤 제거, 추적 파라미터 제거, 도메인 소문자."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def publisher_of(url: str) -> str:
    """URL의 도메인으로 언론사 이름을 찾는다(하위 도메인도 인정). 모르면 도메인을 돌려준다."""
    host = urlsplit(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    for domain, name in PUBLISHERS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def to_iso(rfc822: str | None) -> str | None:
    """RSS의 날짜 형식(RFC 822, 예: "Tue, 04 Nov 2026 10:00:00 +0900")을 ISO 문자열로. 읽지 못하면 None.
    시간대가 없으면 UTC로 간주한다."""
    if not rfc822:
        return None
    try:
        dt = parsedate_to_datetime(rfc822.strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def is_volleyball(title: str, summary: str) -> bool:
    """제목이나 요약에 배구 관련 단어가 있는가."""
    return bool(_VOLLEY.search(title) or _VOLLEY.search(summary))


def article(*, url, title, summary, publisher, published_at, source, feed) -> dict:
    """Kafka로 보낼 기사 메타데이터 하나. URL은 정규화하고 요약은 잘라 둔다."""
    return {"url": canonical_url(url), "title": title, "summary": short(summary), "publisher": publisher,
            "published_at": published_at, "source": source, "feed": feed}


def parse_rss(xml_text: str | bytes, feed: str, publisher: str) -> list[dict]:
    """RSS 2.0 <item> 목록을 기사 딕셔너리로 바꾼다(모든 종목 그대로. 배구 여부는 is_volleyball로 거른다).
    제목이 없거나 링크가 http로 시작하지 않는 항목은 버린다."""
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = plain_text(item.findtext("title"))
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not link.startswith("http"):
            continue
        out.append(article(url=link, title=title, summary=plain_text(item.findtext("description")),
                           publisher=publisher, published_at=to_iso(item.findtext("pubDate")),
                           source="RSS", feed=feed))
    return out


def parse_naver(body: dict, query: str) -> list[dict]:
    """네이버 뉴스 검색 API 응답을 기사 딕셔너리로 바꾼다. 원문 링크(originallink)를 우선 쓴다."""
    out = []
    for item in body.get("items", []):
        url = item.get("originallink") or item.get("link") or ""
        if not url.startswith("http"):
            continue
        out.append(article(url=url, title=plain_text(item.get("title")), summary=plain_text(item.get("description")),
                           publisher=publisher_of(url), published_at=to_iso(item.get("pubDate")),
                           source="NAVER_API", feed=f"naver:{query}"))
    return out


class NewsHttp:
    """뉴스용 HTTP 클라이언트: 정직한 User-Agent, 1초에 1번, 네트워크/5xx 오류는 몇 번 재시도."""

    def __init__(self, session=None, limiter=None, retries=2, timeout=15):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.limiter = limiter or RateLimiter(1.0)
        self.retries = retries
        self.timeout = timeout

    def get(self, url, **kwargs):
        """GET 요청. 4xx는 바로 예외, 5xx·네트워크 오류는 2초, 4초... 기다렸다가 재시도한다."""
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
                if resp.status_code < 500 or attempt == self.retries:
                    resp.raise_for_status()
                    return resp
            except requests.RequestException:
                if attempt == self.retries:
                    raise
            time.sleep(2 * (attempt + 1))
        raise RuntimeError("unreachable")


class NewsCollector(BaseCollector):
    """실행 한 번 = 모든 RSS 피드(+ 키가 있으면 네이버 검색어들)를 한 바퀴 돈다.

    피드 하나가 실패하면 기록만 하고 넘어간다(한 사이트가 죽어도 나머지는 계속 모음).
    모든 출처가 실패했을 때만 실행 전체를 실패로 본다.
    """

    name = "news_collect"
    topic = "news.article"
    source = "news"

    def __init__(self, client: NewsHttp, publisher, feeds=None, naver_id=None, naver_secret=None):
        """feeds/naver_id/naver_secret을 넘기면 설정 대신 그것을 쓴다(테스트용)."""
        super().__init__(client, publisher)
        self.feeds = RSS_FEEDS if feeds is None else feeds
        self.naver_id = settings.NAVER_CLIENT_ID if naver_id is None else naver_id
        self.naver_secret = settings.NAVER_CLIENT_SECRET if naver_secret is None else naver_secret
        self.urls: list[str] = []  # 최근 실행이 보낸 URL들(DAG가 이것들이 검색 색인에 들어갔는지 확인한다)
        # url -> 제목: enricher는 제목이 같은 기사를 하나만 남기므로, DAG가 저장 여부를 확인할 때 제목도 필요하다
        self.titles: dict[str, str] = {}
        self.failures: list[str] = []  # 실패한 출처 목록("피드: 오류 종류")

    def describe(self, **params):
        return f"rss={len(self.feeds)} naver={'on' if self.naver_id else 'off'}"

    def sources(self):
        """(출처 이름, URL, 가져오기 함수) 를 하나씩 내보낸다. 가져오기는 collect()에서 출처마다 따로 시도한다.
        람다의 기본 인자(url=url 등)는 반복문 변수를 그 시점 값으로 묶어 두기 위한 것이다."""
        for feed, publisher, url in self.feeds:
            yield feed, url, lambda url=url, feed=feed, publisher=publisher: parse_rss(
                self.client.get(url).content, feed, publisher)
        if self.naver_id and self.naver_secret:
            headers = {"X-Naver-Client-Id": self.naver_id, "X-Naver-Client-Secret": self.naver_secret}
            for q in NAVER_QUERIES:
                params = {"query": q, "display": 100, "sort": "date"}
                yield f"naver:{q}", NAVER_URL, lambda q=q, params=params: parse_naver(
                    self.client.get(NAVER_URL, params=params, headers=headers).json(), q)

    def collect(self, **params):
        """출처마다 기사를 받아 배구 기사만, 이번 실행에서 처음 본 URL만 내보낸다."""
        self.urls, self.failures, self.titles = [], [], {}
        tried = 0
        seen = set()  # 이번 실행 안에서의 중복(여러 피드에 같은 기사) 제거용
        for feed, url, fetch in self.sources():
            tried += 1
            try:
                items = fetch()
            except Exception as exc:  # 피드 하나가 고장 나도 나머지는 계속한다
                log.warning("feed %s failed: %s", feed, exc)
                self.failures.append(f"{feed}: {exc.__class__.__name__}")
                continue
            kept = [a for a in items if is_volleyball(a["title"], a["summary"]) and a["url"] not in seen]
            log.info("feed %s: %d items, %d volleyball", feed, len(items), len(kept))
            for a in kept:
                seen.add(a["url"])
                self.urls.append(a["url"])
                self.titles[a["url"]] = a["title"]
                yield url, {"feed": feed}, a
        if tried and len(self.failures) == tried:
            raise RuntimeError(f"every news source failed: {self.failures}")

    def key_of(self, endpoint, request_params, body):
        return body["url"]
