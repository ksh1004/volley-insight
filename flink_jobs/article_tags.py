"""기사에 팀·선수 태그를 달고, 중복 판정용 키를 만든다(article_enricher.py가 사용. Flink import 없음 → 단위 테스트 가능).

태그는 제목과 요약만 보고 단다(저장하는 것이 그것뿐이므로).
- 팀: 팀의 현재 짧은 이름 + 알려진 별칭·애칭(TEAM_ALIASES). 이름은 '단어의 시작'에 있을 때만 인정한다.
  그래서 "IBK기업은행" 안의 "기업은행"이 두 번째로 잡히지 않고, "IBK"가 "IBKS"에 걸리지 않는다.
- 선수: 최근 약 두 시즌 안에 경기에 나온 선수를 가장 최근 소속 팀과 함께 등록한다.
  세 글자 이상 이름은 그것만으로 인정한다. 두 글자 이름(레오, 실바, 모마 같은 외국인 선수가 많음)은 흔한 단어거나
  다른 단어의 일부일 수 있어서, 그 선수의 팀도 함께 언급될 때만 인정한다.
  이름이 같은 선수가 둘이면 팀이 언급된 쪽을 고르고, 그래도 못 정하면 둘 다 달지 않는다(틀린 태그보다 빠진 태그가 낫다).
"""
import re
import unicodedata

# DB에 있는 팀의 현재 이름 외의 별칭(KOVO 팀 코드별). 팀 이름은 후원사에 따라 바뀌므로 DB 이름도 항상 함께 쓴다
# (Dictionary를 만들 때 load_dictionary가 더함). 1xxx는 남자부, 2xxx는 여자부 팀이다.
TEAM_ALIASES = {
    "1001": ["대한항공", "점보스"],
    "1002": ["삼성화재", "블루팡스"],
    "1004": ["KB손해보험", "KB손보", "KB스타즈"],
    "1005": ["현대캐피탈", "현캐", "스카이워커스"],
    "1006": ["한국전력", "한전", "빅스톰", "KEPCO"],
    "1008": ["OK저축은행", "OK읏맨", "읏맨", "OK금융그룹"],
    "1009": ["우리카드", "우리WON"],
    "2001": ["현대건설", "힐스테이트"],
    "2002": ["한국도로공사", "도로공사", "하이패스"],
    "2003": ["정관장", "레드스파크스", "KGC인삼공사"],
    "2004": ["흥국생명", "핑크스파이더스"],
    "2005": ["GS칼텍스", "KIXX"],
    "2006": ["IBK기업은행", "기업은행", "IBK", "알토스"],
    "2008": ["SOOP"],
}
# 보통 단어이기도 한 두 글자 선수 이름: 절대 태그하지 않는다
NAME_STOPWORDS = {"노란", "피치", "이든"}

# '단어의 시작': 바로 앞 글자가 한글·영문·숫자가 아니어야 한다(정규식의 부정 후방 탐색)
_WORD_START = r"(?<![가-힣A-Za-z0-9])"


def _pattern(names):
    """이름 목록을 하나의 정규식으로 만든다. 긴 이름부터 시도하게 정렬하고(짧은 이름이 먼저 잡히지 않게),
    영문·숫자로 끝나는 이름은 뒤에 영문·숫자가 이어지지 않을 때만 인정한다("IBK" ≠ "IBKS")."""
    parts = []
    for n in sorted(set(names), key=len, reverse=True):
        p = re.escape(n)
        if re.search(r"[A-Za-z0-9]$", n):
            p += r"(?![A-Za-z0-9])"
        parts.append(p)
    return re.compile(_WORD_START + "(?:" + "|".join(parts) + ")", re.IGNORECASE) if parts else None


def normalize(text: str) -> str:
    """한글 유니코드 정규화(NFC). 자모가 분리된 채로 온 글자도 같은 문자열로 비교되게 한다."""
    return unicodedata.normalize("NFC", text or "")


def title_key(title: str) -> str | None:
    """"같은 기사, 다른 URL"을 찾기 위한 키: 앞의 [말머리]와 (종합)·(속보) 같은 꼬리표를 떼고 글자·숫자만 남긴다.
    너무 짧은 제목("[프로배구 전적] 3일" 등)은 서로 다른 기사도 같아지기 쉬워 키를 주지 않는다(None)."""
    t = normalize(title)
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t)  # 앞쪽 [말머리]들 제거
    t = re.sub(r"\((종합|속보|\d보|상보|영상|포토)[^)]*\)", "", t)  # (종합), (2보) 등 제거
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()  # 공백·문장부호 제거
    return t if len(t) >= 10 else None


class Dictionary:
    """태그 사전. teams: [(코드, 이름, [별칭])], players: [(코드, 이름, 팀 코드)]"""

    def __init__(self, teams, players):
        self.team_names = {code: name for code, name, _ in teams}
        self.team_patterns = []  # [(팀 코드, 그 팀 이름·별칭 정규식)]
        for code, name, aliases in teams:
            pat = _pattern([name, *aliases])
            if pat:
                self.team_patterns.append((code, pat))
        by_name = {}  # 선수 이름 -> [(선수 코드, 팀 코드)] (동명이인은 여러 개)
        for code, name, team_code in players:
            name = normalize(name).strip()
            # 한 글자 이름, 금지어, 한글이 아닌 이름은 빼고 등록
            if len(name) < 2 or name in NAME_STOPWORDS or not re.fullmatch(r"[가-힣]+", name):
                continue
            by_name.setdefault(name, []).append((code, team_code))
        self.players_by_name = by_name
        self.player_pattern = _pattern(by_name)

    def tag(self, title: str, summary: str = ""):
        """제목+요약에서 찾은 (팀 코드들, 선수 코드들, 선수 이름들)을 돌려준다(각각 정렬됨). 규칙은 모듈 설명 참고."""
        text = normalize(f"{title} {summary}")
        teams = {code for code, pat in self.team_patterns if pat.search(text)}
        players = {}  # 선수 코드 -> 이름
        for name in {m.group(0) for m in self.player_pattern.finditer(text)} if self.player_pattern else ():
            candidates = self.players_by_name.get(name, [])
            with_team = [c for c in candidates if c[1] in teams]  # 소속 팀도 언급된 후보
            if len(candidates) == 1 and (len(name) >= 3 or with_team):
                players[candidates[0][0]] = name  # 유일한 이름: 세 글자 이상이거나 팀이 함께 나오면 인정
            elif len(with_team) == 1:
                players[with_team[0][0]] = name  # 동명이인: 팀이 언급된 사람이 하나로 정해지면 인정
        return sorted(teams), sorted(players), [players[c] for c in sorted(players)]


TEAMS_SQL = "SELECT code, name FROM team WHERE is_active"
# 최근 선수마다 '가장 최근 경기의 소속 팀'을 붙인다(LATERAL 서브쿼리로 선수별 최신 경기 1건).
# current: 최근 약 1년(400일) 안에 뛴 선수인지. 전체 대상은 약 2년(760일).
PLAYERS_SQL = """
    SELECT p.code, p.name, t.code, p.last_seen_on >= current_date - 400 AS current
    FROM player p
    JOIN LATERAL (
        SELECT s.team_id FROM player_match_stat s JOIN match m ON m.id = s.match_id
        WHERE s.player_id = p.id ORDER BY m.scheduled_at DESC LIMIT 1
    ) last ON true
    JOIN team t ON t.id = last.team_id
    WHERE p.last_seen_on >= current_date - 760
"""


def load_dictionary(conn) -> Dictionary:
    """PostgreSQL에서 팀·선수를 읽어 태그 사전을 만든다."""
    with conn.cursor() as cur:
        cur.execute(TEAMS_SQL)
        teams = [(code, name, TEAM_ALIASES.get(code, [])) for code, name in cur.fetchall()]
        cur.execute(PLAYERS_SQL)
        # 두 글자 이름은 최근 약 한 시즌 선수만(더 오래된 선수까지 넣으면 잘못 걸리는 일이 너무 많다)
        players = [(code, name, team) for code, name, team, current in cur.fetchall()
                   if current or len((name or "").strip()) >= 3]
    return Dictionary(teams, players)
