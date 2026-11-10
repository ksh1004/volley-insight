"""실제 KOVO 응답으로 하는 회귀 테스트. 응답은 `python -m collectors probe / schedule --save-sample`로
collectors/samples/에 저장해 둔 것이다(파일이 없으면 그 테스트는 건너뛴다).

가짜 응답으로 만든 다른 테스트들이 놓칠 수 있는 "실제 응답 모양"을 확인한다: 시즌 코드의 시작 연도,
걸러야 할 리그 코드, 일정 페이지 구조, 경기 상세의 박스스코어 필드, 랠리 기록 페이지 나눔.
"""
import json
from pathlib import Path

import pytest

from collectors.kovo_collectors import RALLY_PAGE_SIZE, Game, is_finished, leagues_of, total_pages

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def load(name):
    """저장된 샘플 JSON을 읽는다. 없으면 테스트를 건너뛴다(skip)."""
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"sample {name} not saved yet")
    return json.loads(path.read_text(encoding="utf-8"))


def test_season_codes_start_at_2005():
    """시즌 코드 001은 2005년(V-리그 첫 시즌)이다."""
    seasons = load("season-list-001.json")["payload"]
    first = next(s for s in seasons if s["seasonCode"] == "001")
    assert first["ryear"] == "2005"


def test_game_leagues_exclude_totals_allstar_and_exhibition():
    """실제 응답에서도 시즌 통산(200)·올스타전(801)·시범경기(901)가 걸러진다."""
    leagues = leagues_of(load("season-list-001.json"), "022")
    assert "201" in leagues
    assert not {"200", "801", "901"} & set(leagues)


def test_schedule_sample_is_one_page_with_game_numbers():
    """2025-26 남자부 정규리그 일정은 한 페이지에 126경기이고, 경기마다 번호·팀·날짜가 있다."""
    body = load("game-schedule-022.json")  # 응답 전체: {"result": ..., "payload": {...}}
    assert total_pages(body) == 1
    games = body["payload"]["content"]
    assert len(games) == 126  # 2025-26 남자부 정규리그
    assert all({"gnum", "hcode", "acode", "gdate"} <= g.keys() for g in games)


def test_finished_game_in_schedule_sample():
    """일정의 첫 경기(3세트 경기)가 끝난 경기로 판정되고 Game으로 바뀐다."""
    game = load("game-schedule-022.json")["payload"]["content"][0]
    assert is_finished(game)
    g = Game.from_schedule(game)
    assert (g.key, g.sets) == ("022-3", 3)


def test_game_detail_sample_has_box_scores():
    """경기 상세에는 두 팀 기록과 선수별 박스스코어(선수 코드·팀 코드·득점)가 있다."""
    body = load("game-detail-022-201-3.json")["payload"]
    assert body["game"]["gnum"] == 3
    assert len(body["team"]) == 2
    players = body["player"]
    assert players and all({"pcode", "tcode", "point"} <= p.keys() for p in players)


def test_full_set_fits_in_one_page_of_500():
    """한 세트의 이벤트는 500개 페이지 하나에 다 들어간다(그래서 대개 세트당 요청 한 번)."""
    feed = load("real-time-022-3-set2.json")["payload"]["feed"]
    assert len(feed) < RALLY_PAGE_SIZE  # 그래서 수집기는 한 페이지 받고 멈춘다
    assert (feed[0]["raction"], feed[-1]["raction"]) == ("ST", "ET")  # 세트 시작 ... 세트 끝


def test_set_split_over_pages_of_200_is_continuous():
    """200개씩 나눠 받아도 첫 페이지는 ST로 시작하고 마지막 페이지는 ET로 끝난다(페이지가 이어진다)."""
    first = load("real-time-022-3-set1.json")["payload"]["feed"]
    second = load("real-time-022-3-set1-p1.json")["payload"]["feed"]
    assert len(first) == 200 and len(second) < 200
    assert first[0]["raction"] == "ST" and second[-1]["raction"] == "ET"
