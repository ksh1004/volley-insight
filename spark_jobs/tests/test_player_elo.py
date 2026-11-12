"""build_marts.py의 선수 Elo(player_elo) 테스트. Spark 없이 돈다: python -m pytest spark_jobs/tests

pyspark는 클러스터 이미지에만 있어 가짜 모듈로 채운다(테스트하는 함수는 pyspark를 쓰지 않는다).
"""
import sys
from collections import namedtuple
from pathlib import Path
from unittest import mock

for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions"):
    sys.modules.setdefault(name, mock.MagicMock())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_marts as b  # noqa: E402

Game = namedtuple("Game", "season_code game_no home_code away_code home_sets away_sets")


def lineup(team, prefix, sets=3):
    """한 팀의 출전 선수 7명(모두 sets세트)."""
    return [(team, f"{prefix}{i}", sets) for i in range(7)]


def test_strength_comes_from_earlier_games_only_and_follows_a_transfer():
    """첫 경기는 두 팀 모두 1500(그 경기 결과를 미리 보지 않음). A가 이기면 A 선수들의 점수가 오르고,
    A의 주전 한 명이 B로 옮겨 뛴 다음부터는 B의 전력에 그 선수 점수가 들어간다."""
    games = [Game("020", 1, "A", "B", 3, 0), Game("020", 2, "A", "B", 3, 0), Game("020", 3, "B", "C", 3, 1)]
    lines = {("020", 1): lineup("A", "a") + lineup("B", "b"), ("020", 2): lineup("A", "a") + lineup("B", "b"),
             ("020", 3): lineup("B", "b")[:6] + [("B", "a0", 4)] + lineup("C", "c", 4)}
    upcoming = [namedtuple("U", "season_code game_no home_code away_code")("020", 4, "B", "A")]
    pre, info = b.player_elo(games, lines, {}, upcoming)
    assert pre[("020", 1)][:2] == (1500.0, 1500.0)
    assert pre[("020", 2)][0] > 1500 > pre[("020", 2)][1]  # A가 1경기를 이겼다
    b_before, b_after = pre[("020", 3)][0], pre[("020", 4)][0]
    assert b_after > b_before  # a0(점수가 높은 선수)가 B 명단에 들어왔다
    assert info["players"] == 21  # a0~a6, b0~b6, c0~c6 (a0는 옮겨도 한 사람)


def test_new_season_regresses_toward_the_players_card():
    """새 시즌 첫 경기 때 선수 점수는 1500이 아니라 앞 시즌 카드(60 = 1500, 1점 = 12 Elo) 쪽으로 절반 되돌아간다."""
    games = [Game("020", 1, "A", "B", 3, 0), Game("021", 1, "A", "B", 3, 0)]
    lines = {k: lineup("A", "a") + lineup("B", "b") for k in [("020", 1), ("021", 1)]}
    cards = {f"b{i}": {"020": 80.0} for i in range(7)}  # B 선수들은 지난 시즌 카드가 아주 좋다(80 → 1740)
    pre, _ = b.player_elo(games, lines, cards)
    home, away, _ = pre[("021", 1)]
    # 1경기에서 져서 1500 아래였지만, 카드(1740)와의 중간쯤(약 1607)에서 시작해 A(카드 없음 → 1500 쪽)보다 강하다
    assert away > 1600 > home
