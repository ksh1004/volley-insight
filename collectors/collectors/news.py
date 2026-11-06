"""Volleyball article metadata from sports RSS feeds and the Naver news search API (COL-07).

Only metadata is kept: title, a short summary, publisher, link and publish time (NFR-02, NFR-06).
Some feeds put the whole article (and images) in <description>, so the summary is cut to SUMMARY_CHARS
characters of plain text *before* publishing; article bodies never reach Kafka or HDFS.
Items that are not about volleyball are dropped here (sports feeds cover every sport).

Each message on `news.article` is one article keyed by its URL. Duplicates across runs and sources are
expected (feeds return the same latest items every hour); Flink `article-enricher` removes them.

Feeds were chosen on 2026-11-04: RSS paths allowed by each site's robots.txt, sports sections only.
The Naver API is used only when NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are set (developers.naver.com).
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
SUMMARY_CHARS = 160

# name, publisher shown on the site, feed URL
RSS_FEEDS = [
    ("yonhap", "연합뉴스", "https://www.yna.co.kr/rss/sports.xml"),
    ("newsis", "뉴시스", "https://www.newsis.com/RSS/sports.xml"),
    ("donga", "동아일보", "https://rss.donga.com/sports.xml"),
    ("hankyung", "한국경제", "https://www.hankyung.com/feed/sports"),
    ("hani", "한겨레", "https://www.hani.co.kr/rss/sports/"),
]
NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
NAVER_QUERIES = ["프로배구", "V리그 배구"]

# Naver gives only the link, so the publisher comes from the domain (unknown domains show the domain).
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

# An article counts as volleyball if its title or summary has one of these. Team nicknames are included
# because headlines often use them without the word 배구.
VOLLEY_WORDS = ["배구", "V리그", "V-리그", "브이리그", "KOVO", "점보스", "스카이워커스", "블루팡스", "빅스톰",
                "읏맨", "힐스테이트", "핑크스파이더스", "알토스", "하이패스", "레드스파크스", "KIXX"]
# "배구조" is never volleyball: 지배구조, 분배구조 ...
_VOLLEY = re.compile("|".join(re.escape(w) + ("(?!조)" if w == "배구" else "") for w in VOLLEY_WORDS),
                     re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|from$)", re.IGNORECASE)


def plain_text(value: str | None) -> str:
    """HTML (possibly escaped twice) -> one line of text."""
    text = html.unescape(value or "")
    text = html.unescape(_TAG.sub(" ", text))
    return _SPACE.sub(" ", _TAG.sub(" ", text)).strip()


def short(text: str, limit: int = SUMMARY_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def canonical_url(url: str) -> str:
    """Same article, same URL: https, no fragment, no tracking parameters."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def publisher_of(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    for domain, name in PUBLISHERS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def to_iso(rfc822: str | None) -> str | None:
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
    return bool(_VOLLEY.search(title) or _VOLLEY.search(summary))


def article(*, url, title, summary, publisher, published_at, source, feed) -> dict:
    return {"url": canonical_url(url), "title": title, "summary": short(summary), "publisher": publisher,
            "published_at": published_at, "source": source, "feed": feed}


def parse_rss(xml_text: str | bytes, feed: str, publisher: str) -> list[dict]:
    """RSS 2.0 items -> article dicts (all sports; filter with is_volleyball)."""
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
    """GET with an honest User-Agent, one request per second, and a few retries on network/5xx errors."""

    def __init__(self, session=None, limiter=None, retries=2, timeout=15):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.limiter = limiter or RateLimiter(1.0)
        self.retries = retries
        self.timeout = timeout

    def get(self, url, **kwargs):
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
    """One run = every RSS feed (+ Naver queries when a key is set). A failing feed is logged and skipped,
    so one site being down does not stop the others; the run fails only if every source failed."""

    name = "news_collect"
    topic = "news.article"
    source = "news"

    def __init__(self, client: NewsHttp, publisher, feeds=None, naver_id=None, naver_secret=None):
        super().__init__(client, publisher)
        self.feeds = RSS_FEEDS if feeds is None else feeds
        self.naver_id = settings.NAVER_CLIENT_ID if naver_id is None else naver_id
        self.naver_secret = settings.NAVER_CLIENT_SECRET if naver_secret is None else naver_secret
        self.urls: list[str] = []  # URLs published by the latest run (the DAG checks they were indexed)
        self.failures: list[str] = []

    def describe(self, **params):
        return f"rss={len(self.feeds)} naver={'on' if self.naver_id else 'off'}"

    def sources(self):
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
        self.urls, self.failures = [], []
        tried = 0
        seen = set()
        for feed, url, fetch in self.sources():
            tried += 1
            try:
                items = fetch()
            except Exception as exc:  # one broken feed must not stop the rest
                log.warning("feed %s failed: %s", feed, exc)
                self.failures.append(f"{feed}: {exc.__class__.__name__}")
                continue
            kept = [a for a in items if is_volleyball(a["title"], a["summary"]) and a["url"] not in seen]
            log.info("feed %s: %d items, %d volleyball", feed, len(items), len(kept))
            for a in kept:
                seen.add(a["url"])
                self.urls.append(a["url"])
                yield url, {"feed": feed}, a
        if tried and len(self.failures) == tried:
            raise RuntimeError(f"every news source failed: {self.failures}")

    def key_of(self, endpoint, request_params, body):
        return body["url"]

