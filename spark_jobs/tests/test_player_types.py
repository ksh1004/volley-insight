"""player_types.py(선수 유형 군집화)의 순수 함수 테스트. Spark 없이 돈다: python -m pytest spark_jobs/tests

선수들을 포지션별로 군집화(k-means)한 뒤, 군집 중심의 특징(z점수)으로 "아웃사이드 히터 · 주포형" 같은
이름을 붙이고, 가까운(비슷한) 선수를 찾는다. 확인하는 것: 옛 포지션 코드 변환, 군집 수 고르기,
특징 이름 붙이기, 같은 포지션 군집 구분, 비슷한 선수 찾기.
"""
import sys
from pathlib import Path

import numpy as np

# spark_jobs 폴더를 import 경로에 넣어 player_types 모듈을 바로 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from player_types import NAMES, choose_k, name_groups, nearest, normalize_position, similarity, traits  # noqa: E402


def centroid(**z):
    """군집 중심: 지정하지 않은 특징은 0(평균)."""
    return {f: z.get(f, 0.0) for f in NAMES}


def test_current_codes_are_kept_and_only_old_ones_mapped():
    """옛 포지션 코드(Li, C 등)만 현재 코드로 바꾸고, 이미 현재 코드면 그대로 둔다."""
    # 정제된 데이터에는 이미 현재 코드가 들어 있다: L은 어느 시즌이든 리베로(두 번 바꾸지 않는다)
    assert normalize_position("018", "L") == "L"
    assert normalize_position("010", "OH") == "OH"
    assert normalize_position("018", "Li") == "L"
    assert normalize_position("018", "C") == "MB"
    assert normalize_position("022", "OH") == "OH"


def test_choose_k_prefers_more_groups_when_almost_as_good():
    """군집 수 k 고르기: 실루엣 점수가 최고점에 거의 가까우면 더 많은 군집(더 세밀한 유형)을 고른다."""
    assert choose_k({4: 0.30, 5: 0.28, 6: 0.25, 7: 0.20}) == 5
    assert choose_k({4: 0.20, 5: 0.40}) == 5


def test_traits_skip_small_differences_and_name_low_errors():
    """평균과 차이가 작은 특징(0.1)은 빼고, 범실은 낮을 때 "범실 적음"이라는 장점으로 부른다."""
    t = traits({**centroid(), "receive_ps": 1.2, "error_ps": -0.8, "block_ps": 0.1})
    assert [x["label"] for x in t] == ["리시브 가담", "범실 적음"]


def test_groups_of_one_position_are_told_apart():
    """같은 포지션의 군집들이 서로 다른 이름을 받는다(전반적으로 낮은 군집은 "보조형")."""
    groups = name_groups([
        {"cluster_no": 0, "position": "OH", "size": 50, "centroid": centroid(receive_ps=1.5, receive_eff=0.8, points_ps=0.5)},
        {"cluster_no": 1, "position": "OH", "size": 50, "centroid": centroid(points_ps=1.6, attack_ps=1.2)},
        {"cluster_no": 2, "position": "OH", "size": 80, "centroid": centroid(points_ps=-0.8, attack_ps=-0.7, dig_ps=-0.9,
                                                                             block_ps=-0.8, receive_ps=-0.5, error_ps=-0.8)},
        {"cluster_no": 3, "position": "L", "size": 30, "centroid": centroid(dig_ps=2.0, receive_ps=1.8)},
    ])
    labels = [g["label"] for g in groups]
    assert labels == ["아웃사이드 히터 · 리시브 가담", "아웃사이드 히터 · 주포형", "아웃사이드 히터 · 보조형", "리베로 · 디그"]
    assert [t["label"] for t in groups[1]["traits"]] == ["득점력", "공격 점유"]


def test_nearest_excludes_the_same_player_and_ranks_by_distance():
    """비슷한 선수는 거리순이며, 같은 선수의 다른 시즌은 빼고, 같은 시즌만 볼 수도 있다. 유사도는 0~100."""
    z = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 5.0], [0.0, 0.05]])
    keys = [("a", "021"), ("b", "021"), ("c", "021"), ("a", "022")]  # a의 다른 시즌은 "비슷한 선수"가 아니다
    near, pairs = nearest(z, keys, same_season_only=False, n=2)
    assert [j for j, _ in near[0]] == [1, 2]
    same, _ = nearest(z, keys, same_season_only=True, n=5)
    assert [j for j, _ in same[3]] == []  # 022 시즌에는 다른 선수가 없다
    assert similarity(0.0, pairs) == 100 and similarity(100.0, pairs) == 0


def test_strengths_name_a_type_before_errors():
    """장점 특징을 먼저, 범실 많음 같은 약점은 뒤에 둔다."""
    t = traits({**centroid(), "error_ps": 1.5, "points_ps": 1.0, "back_ps": 0.9})
    assert [x["label"] for x in t] == ["득점력", "후위 공격", "범실 많음"]


def test_a_group_with_nothing_special_is_balanced():
    """같은 포지션 안에서 두드러진 특징이 없는 군집은 "균형형"이라 부른다."""
    groups = name_groups([
        {"cluster_no": 0, "position": "OP", "size": 50, "centroid": centroid(points_ps=2.8, back_ps=3.3)},
        {"cluster_no": 1, "position": "OP", "size": 50, "centroid": centroid(points_ps=1.2, back_ps=1.4, error_ps=-0.5)},
    ])
    assert [g["label"] for g in groups] == ["아포짓 · 주포형", "아포짓 · 균형형"]
