"""player_ratings.py(선수 능력치 카드)의 순수 함수 테스트. Spark 없이 돈다: python -m pytest spark_jobs/tests

능력치 점수 = 60 + 12 × z점수(1~99로 자름). 시도가 적은 선수는 평균 쪽으로 당긴다(축소 추정).
확인하는 것: 점수 눈금, 축소, 포지션 그룹 안 순위, 랠리 기록 지표와 박스스코어 지표의 선택,
세터 기여도 계산, 기준 시즌 범위(이번 시즌 + 앞 2시즌), 종합 점수, 통산 카드.
"""
import sys
from pathlib import Path

# spark_jobs 폴더를 import 경로에 넣어 player_ratings 모듈을 바로 불러온다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import player_ratings as pr  # noqa: E402


def libero(i, season="022", qualified=True, rec=(100, 200), digs=(150, 200), sets=80, set_att=20):
    """리베로 한 시즌 기록 행. rec = (리시브 정확, 리시브 시도), digs = (디그 성공, 디그 시도)."""
    return {"season_code": season, "player_code": f"L{i}", "team_code": "T", "gender": "F", "position": "L",
            "sets": sets, "qualified": qualified, "receive_excellent": rec[0], "receive_attempts": rec[1],
            "dig_success": digs[0], "dig_attempts": digs[1], "set_attempts": set_att}


def test_score_scale_and_clipping():
    """z=0 → 60, z=±1 → 72/48, 범위 밖은 1~99로 자르고, 값이 없으면 None."""
    assert pr.to_score(0) == 60
    assert pr.to_score(1) == 72
    assert pr.to_score(-1) == 48
    assert pr.to_score(10) == 99 and pr.to_score(-10) == 1
    assert pr.to_score(None) is None


def test_few_attempts_are_pulled_to_the_average():
    """3번 중 3번 성공은 100%로 보지 않고 리그 평균(0.4) 근처로 당긴다. 시도가 많으면 거의 그대로."""
    few = {"receive_excellent": 3, "receive_attempts": 3}
    many = {"receive_excellent": 300, "receive_attempts": 300}
    assert abs(pr.shrunk_value(few, "rec_exc", 0.4) - 0.4) < 0.05       # 3/3이라고 100% 리시버는 아니다
    assert pr.shrunk_value(many, "rec_exc", 0.4) > 0.8
    # 세터 기여도(기대보다 얼마나 더 나았나)는 리그 평균이 아니라 0 쪽으로 당긴다
    assert pr.shrunk_value({"set_added": 30, "pbp_sets": 300}, "setter_added", 0.5) == 30 / 600


def test_liberos_ranked_by_reception_and_average_near_60():
    """리시브가 좋을수록 점수가 높고, 기준 선수들의 평균은 60 근처다. 표본이 작은 신인은 축소돼 1등이 못 된다."""
    group = [libero(i, rec=(80 + 6 * i, 200), digs=(140 + i, 200)) for i in range(12)]
    rookie = libero(99, qualified=False, rec=(5, 5), sets=12)  # 아주 작은 표본에서 완벽한 기록
    scored = pr.score_group(group, group + [rookie], "L")
    receive = {r["player_code"]: scored[id(r)]["abilities"][0]["score"] for r in group + [rookie]}
    assert receive["L11"] > receive["L5"] > receive["L0"]
    assert abs(sum(receive[f"L{i}"] for i in range(12)) / 12 - 60) <= 1
    assert receive["L99"] < receive["L11"]  # 축소돼서 최고 리시버가 아니다
    best = scored[id(group[11])]
    assert best["abilities"][0]["stats"][0] == {"metric": "rec_exc", "label": "리시브 정확", "unit": "pct",
                                                 "value": (80 + 66) / 200}
    assert best["overall"] is not None


def test_play_by_play_version_first_box_score_otherwise():
    """랠리 기록이 있는 시즌은 서브 압박(serve_pressure)을, 없는 옛 시즌은 박스스코어 지표(serve_net)를 쓴다."""
    def oh(i, season, serves):
        """아웃사이드 히터 한 시즌 기록. serves=True면 랠리 기록 기반 서브 지표도 채운다."""
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
    """세터 기여도: 같은 공격수가 세터 A의 토스와 B의 토스에서 낸 성공률을 비교한다."""
    setters = {("022", "A"), ("022", "B")}
    # 행: (시즌, 세터, 공격수, 공격 수, 성공 수)
    rows = [("022", "A", "X", 100, 60), ("022", "B", "X", 100, 40),   # X는 A의 토스에서 60%, B의 토스에서 40% 성공
            ("022", "A", "Y", 50, 25)]                                 # Y는 A의 토스만 때렸다: 비교 대상 없음
    added = pr.setter_added(rows, setters)
    a, an = added[("022", "A")]
    b, bn = added[("022", "B")]
    assert an == 150 and bn == 100
    assert a > 0 > b
    # Y의 50번은 기여도에 거의 영향이 없다: 비교할 다른 세터가 없어 기준이 Y 자신의 성공률이 되기 때문
    assert abs(pr.setter_added([("022", "A", "Y", 50, 25)], setters)[("022", "A")][0]) < 1e-9


def test_reference_window_is_this_season_and_two_before():
    """점수 기준은 이번 시즌 + 앞 2시즌의 선수들이다. 기준 인원이 MIN_REF보다 적으면 점수를 매기지 않는다."""
    rows = [libero(i, season=s, rec=(80 + 6 * i, 200)) for s in ("018", "019", "020", "021", "022") for i in range(4)]
    for r in rows:
        r["player_code"] += r["season_code"]
    out = pr.score_all(rows)
    assert len(out) == 20
    first = [res for r, res in out if r["season_code"] == "018"]
    assert all(res["overall"] is None for res in first)  # 4명뿐: MIN_REF 미만
    last = [res for r, res in out if r["season_code"] == "022"]
    assert all(res["abilities"][0]["score"] is not None for res in last)  # 020~022 = 12명


def test_overall_is_the_weighted_average_of_the_card_scores():
    """종합 점수 = 카드 능력치들의 가중 평균. 가중치 0은 빼고, 빠진 점수가 있으면 나머지로, 너무 많이 빠지면 None."""
    abilities = [{"weight": 60, "score": 75}, {"weight": 40, "score": 79}, {"weight": 0, "score": 20}]
    assert pr.overall_score(abilities) == round((60 * 75 + 40 * 79) / 100)  # 가중치 0인 능력치는 세지 않는다
    assert pr.overall_score([{"weight": 60, "score": 75}, {"weight": 40, "score": None}]) == 75  # 하나 빠짐: 괜찮다
    assert pr.overall_score([{"weight": 50, "score": None}, {"weight": 25, "score": None}, {"weight": 25, "score": 70}]) is None


def test_career_card_averages_seasons_by_sets_and_pools_the_record():
    """통산 카드: 능력치 점수는 시즌 점수를 출전 세트 수로 가중 평균하고, 기록은 시즌들을 합쳐서 다시 계산한다.
    랠리 기록 지표와 박스스코어 지표가 시즌마다 다르면 각각 기간 표시("~부터", "~까지")를 붙인다."""
    def season(code, sets, qualified, serve_metric, serve_score, rec_score, rec, serves):
        """미들 블로커 한 시즌의 (기록 행, 카드). 첫 능력치(블로킹)와 넷째(서브)에만 점수·지표를 넣는다."""
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
    assert block["score"] == round((100 * 80 + 50 * 50 + 10 * 99) / 160)  # 출전 세트 수로 가중
    assert serve["score"] == round((100 * 70 + 50 * 40 + 10 * 99) / 160)
    assert block["stats"][0]["value"] == (50 + 25 + 5) / 160 and "period" not in block["stats"][0]  # 전 시즌 합산
    by_metric = {s["metric"]: s for s in serve["stats"]}
    assert by_metric["serve_pressure"]["value"] == 40 / 120 and by_metric["serve_pressure"]["period"] == "2023–24부터"
    assert by_metric["ace_ps"]["value"] == 10 / 100 and by_metric["ace_ps"]["period"] == "2022–23까지"
    assert (card["first_season"], card["last_season"], card["seasons"], card["sets"]) == ("019", "022", 3, 160)
    assert (card["best_season"], card["best_overall"]) == ("019", 80)  # 99점인 짧은 시즌은 규정 미달이라 제외
    assert card["qualified_seasons"] == 2 and not card["qualified"]  # 통산 순위에 들려면 규정 시즌 3개가 필요
    assert card["overall"] == pr.overall_score(card["abilities"])
