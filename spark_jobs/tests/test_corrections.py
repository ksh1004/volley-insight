"""curate_kovo.py의 수동 수정(ADM-03) 키 해석 테스트. Spark 없이 돈다: python -m pytest spark_jobs/tests

관리자가 화면에서 입력한 "대상 + 키"(예: set_score "023-5/3")를 정제 테이블의 기본 키 열로 바꾸는 규칙과,
수정들을 테이블별로 묶는 계획(correction_plan), 품질 규칙 목록이 Django 쪽과 같은지를 확인한다.
pyspark는 클러스터 이미지에만 설치돼 있어 여기서는 가짜 모듈(MagicMock)로 채운다. 테스트하는 함수들은 pyspark를 쓰지 않는다.
"""
import sys
from pathlib import Path
from unittest import mock

for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions", "pyspark.sql.types"):
    sys.modules.setdefault(name, mock.MagicMock())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curate_kovo import BLOCKING, correction_key, correction_plan  # noqa: E402


def test_keys_of_each_target():
    """대상마다 키 형식: 선수 "코드", 경기 "시즌-번호", 세트 "시즌-번호/세트", 선수·팀 경기 기록 "시즌-번호/코드"."""
    assert correction_key("player", " 1000525 ") == {"code": "1000525"}
    assert correction_key("match", "023-5") == {"season_code": "023", "game_no": 5}
    assert correction_key("set_score", "023-5/3") == {"season_code": "023", "game_no": 5, "set_no": 3}
    assert correction_key("player_match_stat", "023-5/0001328") == {"season_code": "023", "game_no": 5,
                                                                     "player_code": "0001328"}
    assert correction_key("team_match_stat", "022-163/1006")["team_code"] == "1006"


def test_malformed_keys_are_skipped():
    """형식이 틀린 키는 None(건너뜀)이다."""
    assert correction_key("match", "023-5/3") is None          # 경기에는 하위 키가 없다
    assert correction_key("set_score", "023-5") is None        # 세트 번호가 빠짐
    assert correction_key("set_score", "023-5/x") is None
    assert correction_key("match", "23-5") is None
    assert correction_key("player_match_stat", "023/1000525") is None


def test_plan_groups_by_curated_table():
    """수정들을 테이블별로 묶고, 잘못된 키·모르는 대상은 버리며, 값 None은 빈 문자열로 바꾼다."""
    plan = correction_plan([("match", "023-5", "spectators", "3120"), ("player", "0001328", "name", "홍길동"),
                            ("set_score", "bad", "home_points", "25"), ("unknown", "x", "y", "z"),
                            ("match", "023-6", "venue", None)])
    assert plan == {
        "match": [({"season_code": "023", "game_no": 5}, "spectators", "3120"),
                  ({"season_code": "023", "game_no": 6}, "venue", "")],
        "player": [({"code": "0001328"}, "name", "홍길동")],
    }


def test_blocking_rules_match_the_django_choices():
    """경기 확정을 막는 규칙(BLOCKING)이 Django ops/models.py의 "# ERROR:" 아래 규칙 목록과 같아야 한다."""
    text =(Path(__file__).resolve().parents[2] / "backend" / "ops" / "models.py").read_text(encoding="utf-8")
    errors = text.split("# ERROR:")[1].split("# WARNING:")[0]
    assert sorted(BLOCKING) == sorted(line.split("=")[0].strip() for line in errors.splitlines()[1:] if "=" in line)
