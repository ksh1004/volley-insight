"""Pure helpers of player_types.py (no Spark needed): python -m pytest spark_jobs/tests"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from player_types import NAMES, choose_k, name_groups, nearest, normalize_position, similarity, traits  # noqa: E402


def centroid(**z):
    return {f: z.get(f, 0.0) for f in NAMES}


def test_current_codes_are_kept_and_only_old_ones_mapped():
    # curated data already has current codes: L is a libero in every season (not mapped twice)
    assert normalize_position("018", "L") == "L"
    assert normalize_position("010", "OH") == "OH"
    assert normalize_position("018", "Li") == "L"
    assert normalize_position("018", "C") == "MB"
    assert normalize_position("022", "OH") == "OH"


def test_choose_k_prefers_more_groups_when_almost_as_good():
    assert choose_k({4: 0.30, 5: 0.28, 6: 0.25, 7: 0.20}) == 5
    assert choose_k({4: 0.20, 5: 0.40}) == 5


def test_traits_skip_small_differences_and_name_low_errors():
    t = traits({**centroid(), "receive_ps": 1.2, "error_ps": -0.8, "block_ps": 0.1})
    assert [x["label"] for x in t] == ["리시브 가담", "범실 적음"]


def test_groups_of_one_position_are_told_apart():
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
    z = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 5.0], [0.0, 0.05]])
    keys = [("a", "021"), ("b", "021"), ("c", "021"), ("a", "022")]  # a's other season is not a "similar player"
    near, pairs = nearest(z, keys, same_season_only=False, n=2)
    assert [j for j, _ in near[0]] == [1, 2]
    same, _ = nearest(z, keys, same_season_only=True, n=5)
    assert [j for j, _ in same[3]] == []  # nobody else in 022
    assert similarity(0.0, pairs) == 100 and similarity(100.0, pairs) == 0


def test_strengths_name_a_type_before_errors():
    t = traits({**centroid(), "error_ps": 1.5, "points_ps": 1.0, "back_ps": 0.9})
    assert [x["label"] for x in t] == ["득점력", "후위 공격", "범실 많음"]


def test_a_group_with_nothing_special_is_balanced():
    groups = name_groups([
        {"cluster_no": 0, "position": "OP", "size": 50, "centroid": centroid(points_ps=2.8, back_ps=3.3)},
        {"cluster_no": 1, "position": "OP", "size": 50, "centroid": centroid(points_ps=1.2, back_ps=1.4, error_ps=-0.5)},
    ])
    assert [g["label"] for g in groups] == ["아포짓 · 주포형", "아포짓 · 균형형"]
