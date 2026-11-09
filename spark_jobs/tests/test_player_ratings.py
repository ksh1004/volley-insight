"""Pure helpers of player_ratings.py (no Spark needed): python -m pytest spark_jobs/tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import player_ratings as pr  # noqa: E402


def libero(i, season="022", qualified=True, rec=(100, 200), digs=(150, 200), sets=80, set_att=20):
    return {"season_code": season, "player_code": f"L{i}", "team_code": "T", "gender": "F", "position": "L",
            "sets": sets, "qualified": qualified, "receive_excellent": rec[0], "receive_attempts": rec[1],
            "dig_success": digs[0], "dig_attempts": digs[1], "set_attempts": set_att}


def test_score_scale_and_clipping():
    assert pr.to_score(0) == 60
    assert pr.to_score(1) == 72
    assert pr.to_score(-1) == 48
    assert pr.to_score(10) == 99 and pr.to_score(-10) == 1
    assert pr.to_score(None) is None


def test_few_attempts_are_pulled_to_the_average():
    few = {"receive_excellent": 3, "receive_attempts": 3}
    many = {"receive_excellent": 300, "receive_attempts": 300}
    assert abs(pr.shrunk_value(few, "rec_exc", 0.4) - 0.4) < 0.05       # 3/3 is not a 100% receiver
    assert pr.shrunk_value(many, "rec_exc", 0.4) > 0.8
    # a setter's difference from expected is shrunk toward 0, not toward the league
    assert pr.shrunk_value({"set_added": 30, "pbp_sets": 300}, "setter_added", 0.5) == 30 / 600


def test_liberos_ranked_by_reception_and_average_near_60():
    group = [libero(i, rec=(80 + 6 * i, 200), digs=(140 + i, 200)) for i in range(12)]
    rookie = libero(99, qualified=False, rec=(5, 5), sets=12)  # perfect in a tiny sample
    scored = pr.score_group(group, group + [rookie], "L")
    receive = {r["player_code"]: scored[id(r)]["abilities"][0]["score"] for r in group + [rookie]}
    assert receive["L11"] > receive["L5"] > receive["L0"]
    assert abs(sum(receive[f"L{i}"] for i in range(12)) / 12 - 60) <= 1
    assert receive["L99"] < receive["L11"]  # shrunk, not the best receiver
    best = scored[id(group[11])]
    assert best["abilities"][0]["stats"][0] == {"metric": "rec_exc", "label": "리시브 정확", "unit": "pct",
                                                 "value": (80 + 66) / 200}
    assert best["overall"] is not None


def test_play_by_play_version_first_box_score_otherwise():
    def oh(i, season, serves):
        r = {"season_code": season, "player_code": f"O{season}{i}", "team_code": "T", "gender": "M", "position": "OH",
             "sets": 90, "qualified": True, "receive_excellent": 100 + i, "receive_attempts": 300, "points": 150 + 5 * i,
             "block_success": 20 + i, "serve_net": i - 10, "serve_attempts": 200, "attack_net": 80 + i,
             "attack_attempts": 300, "dig_success": 100 + i, "serves": None, "serve_hits": None}
        if serves:
            r["serves"], r["serve_hits"] = 200, 60 + 3 * i
        return r
    old = [oh(i, "019", False) for i in range(10)]
    new = [oh(i, "020", True) for i in range(10)]
    scored = pr.score_group(old + new, new, "OH")
    serve = scored[id(new[3])]["abilities"][3]
    assert serve["key"] == "serve" and serve["stats"][0]["metric"] == "serve_pressure"
    scored_old = pr.score_group(old, old, "OH")
    assert scored_old[id(old[3])]["abilities"][3]["stats"][0]["metric"] == "serve_net"


def test_setter_added_compares_the_same_attackers():
    setters = {("022", "A"), ("022", "B")}
    rows = [("022", "A", "X", 100, 60), ("022", "B", "X", 100, 40),   # X kills 60% on A's balls, 40% on B's
            ("022", "A", "Y", 50, 25)]                                 # Y only hit A's sets: nothing to compare
    added = pr.setter_added(rows, setters)
    a, an = added[("022", "A")]
    b, bn = added[("022", "B")]
    assert an == 150 and bn == 100
    assert a > 0 > b
    # Y's 50 sets add about nothing: his baseline falls back to his own rate
    assert abs(pr.setter_added([("022", "A", "Y", 50, 25)], setters)[("022", "A")][0]) < 1e-9


def test_reference_window_is_this_season_and_two_before():
    rows = [libero(i, season=s, rec=(80 + 6 * i, 200)) for s in ("018", "019", "020", "021", "022") for i in range(4)]
    for r in rows:
        r["player_code"] += r["season_code"]
    out = pr.score_all(rows)
    assert len(out) == 20
    first = [res for r, res in out if r["season_code"] == "018"]
    assert all(res["overall"] is None for res in first)  # only 4 players: below MIN_REF
    last = [res for r, res in out if r["season_code"] == "022"]
    assert all(res["abilities"][0]["score"] is not None for res in last)  # 020-022 = 12 players


def test_overall_is_the_weighted_average_of_the_card_scores():
    abilities = [{"weight": 60, "score": 75}, {"weight": 40, "score": 79}, {"weight": 0, "score": 20}]
    assert pr.overall_score(abilities) == round((60 * 75 + 40 * 79) / 100)  # the 0-weight ability is not counted
    assert pr.overall_score([{"weight": 60, "score": 75}, {"weight": 40, "score": None}]) == 75  # one missing: ok
    assert pr.overall_score([{"weight": 50, "score": None}, {"weight": 25, "score": None}, {"weight": 25, "score": 70}]) is None


def test_career_card_averages_seasons_by_sets_and_pools_the_record():
    def season(code, sets, qualified, serve_metric, serve_score, rec_score, rec, serves):
        row = {"season_code": code, "player_code": "P", "gender": "M", "position": "MB", "sets": sets,
               "qualified": qualified, "receive_excellent": rec[0], "receive_attempts": rec[1],
               "serve_hits": serves[0], "serves": serves[1], "serve_aces": 10, "set_success": 0}
        abil = [{"key": k, "label": k, "weight": w, "score": None, "stats": []} for k, _, w, _ in pr.ABILITIES["MB"]]
        abil[0].update(score=rec_score, stats=[{"metric": "block_ps"}])
        abil[3].update(score=serve_score, stats=[{"metric": serve_metric}])
        row.update(block_success=sets // 2)
        return row, {"overall": rec_score, "abilities": abil}

    old = season("019", 100, True, "ace_ps", 70, 80, (0, 0), (0, 0))
    new = season("021", 50, True, "serve_pressure", 40, 50, (0, 0), (30, 100))
    short = season("022", 10, False, "serve_pressure", 99, 99, (0, 0), (10, 20))
    [card] = pr.career_cards([new, short, old])
    block, _, _, serve = card["abilities"]
    assert block["score"] == round((100 * 80 + 50 * 50 + 10 * 99) / 160)  # weighted by sets played
    assert serve["score"] == round((100 * 70 + 50 * 40 + 10 * 99) / 160)
    assert block["stats"][0]["value"] == (50 + 25 + 5) / 160 and "period" not in block["stats"][0]  # pooled
    by_metric = {s["metric"]: s for s in serve["stats"]}
    assert by_metric["serve_pressure"]["value"] == 40 / 120 and by_metric["serve_pressure"]["period"] == "2023–24부터"
    assert by_metric["ace_ps"]["value"] == 10 / 100 and by_metric["ace_ps"]["period"] == "2022–23까지"
    assert (card["first_season"], card["last_season"], card["seasons"], card["sets"]) == ("019", "022", 3, 160)
    assert (card["best_season"], card["best_overall"]) == ("019", 80)  # the short 99 season is not qualified
    assert card["qualified_seasons"] == 2 and not card["qualified"]  # 3 qualified seasons to be ranked all-time
    assert card["overall"] == pr.overall_score(card["abilities"])
