"""뉴스 수집기(collectors/news.py) 테스트.

기사는 메타데이터(제목·링크·언론사·시각·짧은 요약)만 저장하고 본문은 절대 옮기지 않는다는 규칙과,
배구 기사만 거르는 필터, 주소 정리(추적 파라미터 제거), 피드 하나가 죽어도 나머지는 계속 도는지를 확인한다.
"""
from collectors.news import (NewsCollector, canonical_url, is_volleyball, parse_naver, parse_rss, plain_text,
                             publisher_of, short)
from collectors.publisher import DryRunPublisher

# RSS 예시: 배구 기사 1건(긴 본문 + 이미지 태그 + 추적 파라미터 붙은 링크)과 배구가 아닌 기사 2건
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title><![CDATA['빅토리아 21점' 여자배구 IBK기업은행, 현대건설 3-0 완파]]></title>
<link>https://www.newsis.com/view/NISX1?utm_source=rss#top</link>
<description><![CDATA[<p><img src="x.jpg"/></p>[화성=뉴시스] 기자 = """ + "본문 " * 200 + """]]></description>
<pubDate>Mon, 03 Nov 2026 21:26:28 +0900</pubDate></item>
<item><title>MLB 시애틀 감독 선임</title><link>https://www.newsis.com/view/NISX2</link></item>
<item><title>FIFA 지배구조위원장</title><link>https://www.newsis.com/view/NISX3</link></item>
</channel></rss>"""


class FakeHttp:
    """requests 흉내: 어떤 주소든 같은 본문(content)을 돌려준다."""
    def __init__(self, body):
        self.body = body

    def get(self, url, **kw):
        class R:
            content = self.body.encode()
        return R()


def test_parse_rss_keeps_metadata_only():
    """링크에서 추적 파라미터·#을 지우고, 시각은 ISO 형식으로, 요약은 태그를 빼고 160자 + "…"로 자른다."""
    items = parse_rss(RSS, "newsis", "뉴시스")
    a = items[0]
    assert a["url"] == "https://www.newsis.com/view/NISX1"
    assert a["published_at"] == "2026-11-03T21:26:28+09:00"
    assert "<" not in a["summary"] and len(a["summary"]) <= 161
    assert a["source"] == "RSS" and a["publisher"] == "뉴시스"


def test_volleyball_filter():
    """배구·팀 이름이 들어간 제목만 통과한다(MLB, FIFA 기사는 제외)."""
    assert is_volleyball("여자배구 IBK기업은행 승리", "")
    assert is_volleyball("대한항공 점보스 연승", "")
    assert not is_volleyball("MLB 시애틀 감독 선임", "")
    assert not is_volleyball("FIFA 지배구조위원장", "")


def test_helpers():
    """태그·HTML 엔티티 제거, 주소 정규화(https, 소문자 호스트, 추적 파라미터 제거), 언론사 이름 찾기, 글자 자르기."""
    assert plain_text("<b>현대건설</b> &amp;quot;승리&amp;quot;") == '현대건설 "승리"'
    assert canonical_url("http://Www.A.com/x?id=1&utm_medium=r&fbclid=z#f") == "https://www.a.com/x?id=1"
    assert publisher_of("https://m.sports.khan.co.kr/a") == "스포츠경향"
    assert publisher_of("https://news.example.org/a") == "news.example.org"
    assert short("가" * 10, 5) == "가" * 5 + "…"


def test_parse_naver():
    """네이버 검색 API 응답: 네이버 링크 대신 원문 링크(originallink)를 쓰고 언론사 이름을 붙인다."""
    body = {"items": [{"title": "<b>프로배구</b> 결과", "originallink": "https://www.yna.co.kr/view/A1",
                       "link": "https://n.news.naver.com/x", "description": "요약", "pubDate": "Tue, 04 Nov 2026 10:00:00 +0900"}]}
    a = parse_naver(body, "프로배구")[0]
    assert a["url"] == "https://www.yna.co.kr/view/A1" and a["publisher"] == "연합뉴스" and a["title"] == "프로배구 결과"


def test_collector_publishes_volleyball_only_and_survives_a_bad_feed():
    """죽은 피드는 failures에 남기고 넘어가며, 배구 기사만 news.article 토픽으로 보낸다(키 = 정리된 주소)."""
    class Broken:  # 주소에 "bad"가 들어가면 연결 실패, 아니면 정상 RSS
        def get(self, url, **kw):
            if "bad" in url:
                raise OSError("down")
            return FakeHttp(RSS).get(url)

    pub = DryRunPublisher()
    c = NewsCollector(Broken(), pub, feeds=[("bad", "x", "https://bad/rss"), ("newsis", "뉴시스", "https://ok/rss")],
                      naver_id="")
    assert c.run() == 1
    assert c.urls == ["https://www.newsis.com/view/NISX1"]
    assert set(c.titles) == set(c.urls) and all(c.titles.values())  # DAG가 합쳐진 기사를 제목으로 찾기 때문에 제목도 남긴다
    assert c.failures == ["bad: OSError"]
    articles = [m for m in pub.messages if m[0] == "news.article"]
    assert articles[0][1] == c.urls[0] and articles[0][2]["source"] == "news"
