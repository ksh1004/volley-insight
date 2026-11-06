"""Team/player tagging and duplicate keys for articles (used by article_enricher.py; no Flink imports).

Tagging looks at the title and summary only (that is all that is stored).
- Teams: the team's current short name plus known aliases and nicknames (TEAM_ALIASES). A name only counts at
  the start of a word, so "기업은행" inside "IBK기업은행" is not a second hit and "IBK" does not match "IBKS".
- Players: players seen in a match in the last ~two seasons, under their latest team.
  Names of three or more letters count on their own. Two-letter names (many foreign players: 레오, 실바, 모마)
  are common words or parts of words, so they count only when the player's team is also mentioned.
  When two players share a name, the one whose team is mentioned is chosen; if that does not decide it,
  neither is tagged (a missing tag is better than a wrong one).
"""
import re
import unicodedata

# Aliases beyond the team's current name in the DB, by KOVO team code. Names change with sponsors, so the
# DB name is always added too (Tagger.load).
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
# Two-letter player names that are ordinary words; never tagged.
NAME_STOPWORDS = {"노란", "피치", "이든"}

_WORD_START = r"(?<![가-힣A-Za-z0-9])"


def _pattern(names):
    parts = []
    for n in sorted(set(names), key=len, reverse=True):
        p = re.escape(n)
        if re.search(r"[A-Za-z0-9]$", n):
            p += r"(?![A-Za-z0-9])"
        parts.append(p)
    return re.compile(_WORD_START + "(?:" + "|".join(parts) + ")", re.IGNORECASE) if parts else None


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def title_key(title: str) -> str | None:
    """Key for "same story, different URL": letters and digits only, without leading [labels] and (종합)-style
    suffixes. Short titles ("[프로배구 전적] 3일") are too generic to merge, so they get no key."""
    t = normalize(title)
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t)
    t = re.sub(r"\((종합|속보|\d보|상보|영상|포토)[^)]*\)", "", t)
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()
    return t if len(t) >= 10 else None


class Dictionary:
    """teams: [(code, name, [aliases])]; players: [(code, name, team_code)]"""

    def __init__(self, teams, players):
        self.team_names = {code: name for code, name, _ in teams}
        self.team_patterns = []
        for code, name, aliases in teams:
            pat = _pattern([name, *aliases])
            if pat:
                self.team_patterns.append((code, pat))
        by_name = {}
        for code, name, team_code in players:
            name = normalize(name).strip()
            if len(name) < 2 or name in NAME_STOPWORDS or not re.fullmatch(r"[가-힣]+", name):
                continue
            by_name.setdefault(name, []).append((code, team_code))
        self.players_by_name = by_name
        self.player_pattern = _pattern(by_name)

    def tag(self, title: str, summary: str = ""):
        """-> (team codes, player codes, player names), each sorted."""
        text = normalize(f"{title} {summary}")
        teams = {code for code, pat in self.team_patterns if pat.search(text)}
        players = {}
        for name in set(m.group(0) for m in self.player_pattern.finditer(text)) if self.player_pattern else ():
            candidates = self.players_by_name.get(name, [])
            with_team = [c for c in candidates if c[1] in teams]
            if len(candidates) == 1 and (len(name) >= 3 or with_team):
                players[candidates[0][0]] = name
            elif len(with_team) == 1:
                players[with_team[0][0]] = name
        return sorted(teams), sorted(players), [players[c] for c in sorted(players)]


TEAMS_SQL = "SELECT code, name FROM team WHERE is_active"
# Each recent player under the team of their latest match.
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
    with conn.cursor() as cur:
        cur.execute(TEAMS_SQL)
        teams = [(code, name, TEAM_ALIASES.get(code, [])) for code, name in cur.fetchall()]
        cur.execute(PLAYERS_SQL)
        # two-letter names only for players of the last ~season (older ones cause too many false hits)
        players = [(code, name, team) for code, name, team, current in cur.fetchall()
                   if current or len((name or "").strip()) >= 3]
    return Dictionary(teams, players)
