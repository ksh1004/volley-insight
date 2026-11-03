"""Regression tests against real KOVO responses saved by `python -m collectors probe / schedule --save-sample`."""
import json
from pathlib import Path

import pytest

from collectors.kovo_collectors import RALLY_PAGE_SIZE, Game, is_finished, leagues_of, total_pages

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def load(name):
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"sample {name} not saved yet")
    return json.loads(path.read_text(encoding="utf-8"))


def test_season_codes_start_at_2005():
    seasons = load("season-list-001.json")["payload"]
    first = next(s for s in seasons if s["seasonCode"] == "001")
    assert first["ryear"] == "2005"


def test_game_leagues_exclude_totals_allstar_and_exhibition():
    leagues = leagues_of(load("season-list-001.json"), "022")
    assert "201" in leagues
    assert not {"200", "801", "901"} & set(leagues)


def test_schedule_sample_is_one_page_with_game_numbers():
    body = load("game-schedule-022.json")  # full response: {"result": ..., "payload": {...}}
    assert total_pages(body) == 1
    games = body["payload"]["content"]
    assert len(games) == 126  # men's regular season 2025-26
    assert all({"gnum", "hcode", "acode", "gdate"} <= g.keys() for g in games)


def test_finished_game_in_schedule_sample():
    game = load("game-schedule-022.json")["payload"]["content"][0]
    assert is_finished(game)
    g = Game.from_schedule(game)
    assert (g.key, g.sets) == ("022-3", 3)


def test_game_detail_sample_has_box_scores():
    body = load("game-detail-022-201-3.json")["payload"]
    assert body["game"]["gnum"] == 3
    assert len(body["team"]) == 2
    players = body["player"]
    assert players and all({"pcode", "tcode", "point"} <= p.keys() for p in players)


def test_full_set_fits_in_one_page_of_500():
    feed = load("real-time-022-3-set2.json")["payload"]["feed"]
    assert len(feed) < RALLY_PAGE_SIZE  # so the collector stops after one page
    assert (feed[0]["raction"], feed[-1]["raction"]) == ("ST", "ET")  # set start ... set end


def test_set_split_over_pages_of_200_is_continuous():
    first = load("real-time-022-3-set1.json")["payload"]["feed"]
    second = load("real-time-022-3-set1-p1.json")["payload"]["feed"]
    assert len(first) == 200 and len(second) < 200
    assert first[0]["raction"] == "ST" and second[-1]["raction"] == "ET"
