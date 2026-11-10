"""실시간 경기 상태 계산(flink_jobs/live_state.py) 테스트. Flink 없이 순수 함수만 부른다.

실제 KOVO 랠리 기록 샘플(collectors/samples)로 공식 점수가 그대로 재현되는지, 같은 이벤트를 여러 번 받거나
잘게 나눠 받아도 결과가 같은지(중복 제거), 득점 이벤트의 해석, 세트·경기 승리 확률 계산을 확인한다.
"""
import json
import sys
from pathlib import Path

import pytest

# flink_jobs 폴더를 import 경로에 넣어 live_state 모듈을 바로 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_state import (apply_batch, match_win_prob, new_state, set_win_prob, summary,  # noqa: E402
                        update_probs)

SAMPLES = Path(__file__).resolve().parents[2] / "collectors" / "samples"
# 팀의 서브권 획득률(sideout: 상대 서브를 받아 득점)과 브레이크율(break: 내 서브로 득점). 승리 확률 계산의 입력
RATES = {"home_sideout": 0.70, "home_break": 0.31, "away_sideout": 0.69, "away_break": 0.32}


def feed(name):
    """저장된 실제 랠리 기록 샘플에서 이벤트 목록(feed)만 꺼낸다."""
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))["payload"]["feed"]


# 022-3 경기: 한국전력(홈 1006) 0:3 우리카드(원정 1009), 세트 점수 20:25, 20:25, 23:25
# 1세트 기록은 200개 페이지 두 개로 나뉘어 저장돼 있어 이어 붙인다
SET1 = feed("real-time-022-3-set1.json") + feed("real-time-022-3-set1-p1.json")
SET2 = feed("real-time-022-3-set2.json")


def state():
    """022-3 경기의 빈 상태(재생 모드)."""
    return new_state("022-3", "1006", "1009", "REPLAY", "test", RATES)


def test_recorded_sets_give_the_official_scores():
    """1·2세트 기록을 넣으면 공식 점수(20:25, 20:25)와 세트 스코어 0:2가 나오고, 홈 승리 확률은 낮다."""
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
    assert out["home_win_prob"] < 0.15  # 0:2로 지고 있음


def test_repeated_or_split_messages_change_nothing():
    """한 번에 받든 7개씩 나눠 받든 결과가 같고, 이미 받은 이벤트를 다시 받으면 무시한다."""
    whole = state()
    points_whole = apply_batch(whole, 1, 0, SET1)
    split = state()
    points_split = []
    for start in range(0, len(SET1), 7):  # 수집기는 15초마다 그 사이 새로 생긴 것만 보낸다
        points_split += apply_batch(split, 1, start, SET1[start:start + 7])
    assert apply_batch(split, 1, 0, SET1) == []  # 전부 다시 보내도 모두 중복이라 새 득점 없음
    assert summary(split) == summary(whole)
    assert len(points_whole) == 45
    assert [p["point_no"] for p in points_split if p["point_no"] == 45]


def test_points_name_scorer_server_and_run():
    """득점마다 득점 팀·서브 팀·득점 종류(공격, 상대 범실 등)·관련 선수를 해석한다."""
    s = state()
    points = apply_batch(s, 1, 0, SET1[:12])
    first = points[0]
    # 첫 랠리: 우리카드가 서브하고, 알리가 공격 성공(AC suc)으로 득점
    assert first["winner"] == "1009" and first["server"] == "1009" and first["kind"] == "ATTACK"
    assert (first["home_score"], first["away_score"]) == (0, 1)
    # 둘째 랠리: 우리카드의 서브 범실(VV erd)이 한국전력의 상대 범실 득점(OP)보다 먼저 기록돼 있다
    second = points[1]
    assert second["kind"] == "OPP_ERROR" and second["winner"] == "1006"
    assert second["action"] == "VV" and second["player_code"] == "1000241"
    assert summary(s)["serving"] == "1006"


def test_set_probability():
    """세트 승리 확률: 대등하면 0.5, 크게 앞서면 1에 가깝고, 듀스에서는 서브를 받는 쪽이 유리하다."""
    assert set_win_prob(0, 0, None, 0.5, 0.5) == pytest.approx(0.5)
    assert set_win_prob(24, 24, "home", 0.5, 0.5) == pytest.approx(0.5)
    assert set_win_prob(24, 20, "home", 0.3, 0.7) > 0.97
    assert set_win_prob(20, 24, "away", 0.3, 0.7) < 0.03
    # 배구에서는 서브하는 쪽이 불리하다(리시브 쪽 득점률 약 70%): 듀스에서는 서브를 받는 쪽이 유리
    assert set_win_prob(24, 24, "home", 0.3, 0.7) < 0.5 < set_win_prob(24, 24, "away", 0.3, 0.7)
    assert set_win_prob(14, 14, "home", 0.5, 0.5, target=15) == pytest.approx(0.5)


def test_match_probability():
    """경기 승리 확률: 세트 스코어를 반영하고, 5세트를 이미 땄으면(set_prob=1.0) 1, 경기가 끝나면 결과대로 0/1."""
    assert match_win_prob(0, 0, 1, None, 0.5, 0.5) == pytest.approx(0.5)
    assert match_win_prob(2, 0, 3, None, 0.3, 0.7) > 0.8
    assert match_win_prob(2, 2, 5, 1.0, 0.3, 0.7) == pytest.approx(1.0)
    s = state()
    s["status"], s["sets"] = "FINISHED", {"home": 1, "away": 3}
    update_probs(s)
    assert s["match_prob"] == 0.0
