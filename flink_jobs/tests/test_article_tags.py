import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from article_tags import TEAM_ALIASES, Dictionary, title_key  # noqa: E402

TEAMS = [("2006", "IBK기업은행", TEAM_ALIASES["2006"]), ("2001", "현대건설", TEAM_ALIASES["2001"]),
         ("1009", "우리카드", TEAM_ALIASES["1009"]), ("1006", "한국전력", TEAM_ALIASES["1006"])]
PLAYERS = [("p1", "빅토리아", "2006"), ("p2", "아라우조", "1009"), ("p3", "레오", "1005"),
           ("p4", "김다은", "2001"), ("p5", "김다은", "2004"), ("p6", "실바", "2006"), ("p7", "노란", "2006")]
D = Dictionary(TEAMS, PLAYERS)


def test_teams_by_name_and_alias_at_word_start():
    teams, _, _ = D.tag("기업은행, 현대건설 3-0 완파", "")
    assert teams == ["2001", "2006"]
    assert D.tag("IBKS 신제품", "")[0] == []           # not the IBK alias
    assert D.tag("'아라우조 20점' 우리카드, 한국전력 3-0 완파")[0] == ["1006", "1009"]


def test_long_player_names_count_alone():
    _, players, names = D.tag("'빅토리아 21점' 여자배구", "")
    assert players == ["p1"] and names == ["빅토리아"]


def test_two_letter_names_need_their_team():
    assert D.tag("레오 활약", "")[1] == []
    assert D.tag("실바 맹활약", "")[1] == []
    assert D.tag("실바 맹활약에 기업은행 승리", "")[1] == ["p6"]
    assert D.tag("노란 유니폼 기업은행", "")[1] == []   # stopword


def test_shared_name_is_resolved_by_team_or_skipped():
    assert D.tag("김다은 활약", "")[1] == []
    assert D.tag("현대건설 김다은 활약", "")[1] == ["p4"]


def test_title_key():
    assert title_key("[프로배구] 기업은행, 현대건설 3-0 완파(종합)") == title_key("기업은행, 현대건설 3-0 완파")
    assert title_key("[프로배구 전적] 3일") is None
