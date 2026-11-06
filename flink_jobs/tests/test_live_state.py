import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_state import (apply_batch, match_win_prob, new_state, set_win_prob, summary,  # noqa: E402
                        update_probs)

SAMPLES = Path(__file__).resolve().parents[2] / "collectors" / "samples"
RATES = {"home_sideout": 0.70, "home_break": 0.31, "away_sideout": 0.69, "away_break": 0.32}


def feed(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))["payload"]["feed"]


# game 022-3: 한국전력 (home 1006) 0:3 우리카드 (away 1009), sets 20:25, 20:25, 23:25
SET1 = feed("real-time-022-3-set1.json") + feed("real-time-022-3-set1-p1.json")
SET2 = feed("real-time-022-3-set2.json")


def state():
    return new_state("022-3", "1006", "1009", "REPLAY", "test", RATES)


def test_recorded_sets_give_the_official_scores():
    s = state()
    apply_batch(s, 1, 0, SET1)
    apply_batch(s, 2, 0, SET2)
    out = summary(s)
    assert out["set_scores"] == [[20, 25], [20, 25]]
    assert (out["home_sets"], out["away_sets"]) == (0, 2)
    assert out["status"] == "LIVE" and not out["set_open"]
    set1 = {m["side"]: m for m in out["metrics"] if m["set"] == 1}
    assert set1["home"]["points"] == 20 and set1["away"]["points"] == 25
    assert 0 < set1["away"]["attack_success_rate"] < 100
    assert out["home_win_prob"] < 0.15  # 0:2 down


def test_repeated_or_split_messages_change_nothing():
    whole = state()
    points_whole = apply_batch(whole, 1, 0, SET1)
    split = state()
    points_split = []
    for start in range(0, len(SET1), 7):  # the collector sends whatever is new every 15 s
        points_split += apply_batch(split, 1, start, SET1[start:start + 7])
    assert apply_batch(split, 1, 0, SET1) == []  # everything again: all duplicates
    assert summary(split) == summary(whole)
    assert len(points_whole) == 45
    assert [p["point_no"] for p in points_split if p["point_no"] == 45]


def test_points_name_scorer_server_and_run():
    s = state()
    points = apply_batch(s, 1, 0, SET1[:12])
    first = points[0]
    # first rally: 우리카드 serves, 알리 (AC suc) scores
    assert first["winner"] == "1009" and first["server"] == "1009" and first["kind"] == "ATTACK"
    assert (first["home_score"], first["away_score"]) == (0, 1)
    # second rally: 우리카드 serve error (VV erd) listed before 한국전력's OP point
    second = points[1]
    assert second["kind"] == "OPP_ERROR" and second["winner"] == "1006"
    assert second["action"] == "VV" and second["player_code"] == "1000241"
    assert summary(s)["serving"] == "1006"


def test_set_probability():
    assert set_win_prob(0, 0, None, 0.5, 0.5) == pytest.approx(0.5)
    assert set_win_prob(24, 24, "home", 0.5, 0.5) == pytest.approx(0.5)
    assert set_win_prob(24, 20, "home", 0.3, 0.7) > 0.97
    assert set_win_prob(20, 24, "away", 0.3, 0.7) < 0.03
    # serving is a disadvantage in volleyball (side-out ~70%): at deuce the receiving side is favoured
    assert set_win_prob(24, 24, "home", 0.3, 0.7) < 0.5 < set_win_prob(24, 24, "away", 0.3, 0.7)
    assert set_win_prob(14, 14, "home", 0.5, 0.5, target=15) == pytest.approx(0.5)


def test_match_probability():
    assert match_win_prob(0, 0, 1, None, 0.5, 0.5) == pytest.approx(0.5)
    assert match_win_prob(2, 0, 3, None, 0.3, 0.7) > 0.8
    assert match_win_prob(2, 2, 5, 1.0, 0.3, 0.7) == pytest.approx(1.0)
    s = state()
    s["status"], s["sets"] = "FINISHED", {"home": 1, "away": 3}
    update_probs(s)
    assert s["match_prob"] == 0.0
