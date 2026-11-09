from collectors.news import (NewsCollector, canonical_url, is_volleyball, parse_naver, parse_rss, plain_text,
                             publisher_of, short)
from collectors.publisher import DryRunPublisher

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title><![CDATA['빅토리아 21점' 여자배구 IBK기업은행, 현대건설 3-0 완파]]></title>
<link>https://www.newsis.com/view/NISX1?utm_source=rss#top</link>
<description><![CDATA[<p><img src="x.jpg"/></p>[화성=뉴시스] 기자 = """ + "본문 " * 200 + """]]></description>
<pubDate>Mon, 03 Nov 2026 21:26:28 +0900</pubDate></item>
<item><title>MLB 시애틀 감독 선임</title><link>https://www.newsis.com/view/NISX2</link></item>
<item><title>FIFA 지배구조위원장</title><link>https://www.newsis.com/view/NISX3</link></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kw):
        class R:
            content = self.body.encode()
        return R()


def test_parse_rss_keeps_metadata_only():
    items = parse_rss(RSS, "newsis", "뉴시스")
    a = items[0]
    assert a["url"] == "https://www.newsis.com/view/NISX1"
    assert a["published_at"] == "2026-11-03T21:26:28+09:00"
    assert "<" not in a["summary"] and len(a["summary"]) <= 161
    assert a["source"] == "RSS" and a["publisher"] == "뉴시스"


def test_volleyball_filter():
    assert is_volleyball("여자배구 IBK기업은행 승리", "")
    assert is_volleyball("대한항공 점보스 연승", "")
    assert not is_volleyball("MLB 시애틀 감독 선임", "")
    assert not is_volleyball("FIFA 지배구조위원장", "")


def test_helpers():
    assert plain_text("<b>현대건설</b> &amp;quot;승리&amp;quot;") == '현대건설 "승리"'
    assert canonical_url("http://Www.A.com/x?id=1&utm_medium=r&fbclid=z#f") == "https://www.a.com/x?id=1"
    assert publisher_of("https://m.sports.khan.co.kr/a") == "스포츠경향"
    assert publisher_of("https://news.example.org/a") == "news.example.org"
    assert short("가" * 10, 5) == "가" * 5 + "…"


def test_parse_naver():
    body = {"items": [{"title": "<b>프로배구</b> 결과", "originallink": "https://www.yna.co.kr/view/A1",
                       "link": "https://n.news.naver.com/x", "description": "요약", "pubDate": "Tue, 04 Nov 2026 10:00:00 +0900"}]}
    a = parse_naver(body, "프로배구")[0]
    assert a["url"] == "https://www.yna.co.kr/view/A1" and a["publisher"] == "연합뉴스" and a["title"] == "프로배구 결과"


def test_collector_publishes_volleyball_only_and_survives_a_bad_feed():
    class Broken:
        def get(self, url, **kw):
            if "bad" in url:
                raise OSError("down")
            return FakeHttp(RSS).get(url)

    pub = DryRunPublisher()
    c = NewsCollector(Broken(), pub, feeds=[("bad", "x", "https://bad/rss"), ("newsis", "뉴시스", "https://ok/rss")],
                      naver_id="")
    assert c.run() == 1
    assert c.urls == ["https://www.newsis.com/view/NISX1"]
    assert set(c.titles) == set(c.urls) and all(c.titles.values())  # the DAG matches merged articles by title
    assert c.failures == ["bad: OSError"]
    articles = [m for m in pub.messages if m[0] == "news.article"]
    assert articles[0][1] == c.urls[0] and articles[0][2]["source"] == "news"
