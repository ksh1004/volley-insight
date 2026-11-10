"""기사 보강 작업의 팀·선수 태깅(flink_jobs/article_tags.py) 테스트. Flink 없이 사전 객체만으로 돈다.

규칙: 팀은 정식 이름이나 별칭(단어 시작에서만)으로 찾는다. 선수는 3글자 이상 이름이면 혼자서도 태그하고,
2글자 이름은 같은 팀이 기사에 함께 나올 때만, 동명이인은 팀으로 구분될 때만 태그한다.
"""
import sys
from pathlib import Path

# flink_jobs 폴더를 import 경로에 넣어 article_tags 모듈을 바로 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from article_tags import TEAM_ALIASES, Dictionary, title_key  # noqa: E402

# 사전 예시: (팀 코드, 이름, 별칭들)과 (선수 코드, 이름, 팀 코드). 김다은은 두 팀에 한 명씩 있는 동명이인
TEAMS = [("2006", "IBK기업은행", TEAM_ALIASES["2006"]), ("2001", "현대건설", TEAM_ALIASES["2001"]),
         ("1009", "우리카드", TEAM_ALIASES["1009"]), ("1006", "한국전력", TEAM_ALIASES["1006"])]
PLAYERS = [("p1", "빅토리아", "2006"), ("p2", "아라우조", "1009"), ("p3", "레오", "1005"),
           ("p4", "김다은", "2001"), ("p5", "김다은", "2004"), ("p6", "실바", "2006"), ("p7", "노란", "2006")]
D = Dictionary(TEAMS, PLAYERS)


def test_teams_by_name_and_alias_at_word_start():
    """별칭("기업은행")도 팀으로 찾지만, 다른 단어의 일부("IBKS")는 아니다. 결과는 팀 코드 순."""
    teams, _, _ = D.tag("기업은행, 현대건설 3-0 완파", "")
    assert teams == ["2001", "2006"]
    assert D.tag("IBKS 신제품", "")[0] == []           # IBK 별칭이 아니다(단어 중간까지 이어짐)
    assert D.tag("'아라우조 20점' 우리카드, 한국전력 3-0 완파")[0] == ["1006", "1009"]


def test_long_player_names_count_alone():
    """3글자 이상 이름은 팀이 함께 안 나와도 태그한다."""
    _, players, names = D.tag("'빅토리아 21점' 여자배구", "")
    assert players == ["p1"] and names == ["빅토리아"]


def test_two_letter_names_need_their_team():
    """2글자 이름은 흔한 단어와 겹치기 쉬워, 그 선수의 팀이 기사에 함께 나와야 태그한다."""
    assert D.tag("레오 활약", "")[1] == []
    assert D.tag("실바 맹활약", "")[1] == []
    assert D.tag("실바 맹활약에 기업은행 승리", "")[1] == ["p6"]
    assert D.tag("노란 유니폼 기업은행", "")[1] == []   # 불용어: 일반 단어로 쓰이는 이름이라 아예 제외


def test_shared_name_is_resolved_by_team_or_skipped():
    """동명이인은 기사에 나온 팀으로 한 명이 정해질 때만 태그하고, 아니면 건너뛴다."""
    assert D.tag("김다은 활약", "")[1] == []
    assert D.tag("현대건설 김다은 활약", "")[1] == ["p4"]


def test_title_key():
    """말머리·(종합) 꼬리표를 떼면 같은 키가 되고, 너무 짧은 제목은 키가 없다(None)."""
    assert title_key("[프로배구] 기업은행, 현대건설 3-0 완파(종합)") == title_key("기업은행, 현대건설 3-0 완파")
    assert title_key("[프로배구 전적] 3일") is None
