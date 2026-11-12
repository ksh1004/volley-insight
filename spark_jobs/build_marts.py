"""정제 테이블로 분석용 마트(요약 테이블)를 만든다(설계서 DFD 3.4~3.5): ANL-01, ANL-02, ANL-05, ANL-11.

  HDFS /curated/kovo/{match,set_score,team_match_stat,player_match_stat,rally_event}  (모든 시즌)
    → team_rating            끝난 경기마다의 팀 Elo 점수(전력 지수)
    → team_season_summary    대회별 팀 성적·순위·지표, 사이드아웃/브레이크율,
                             문자중계로 계산한 리시브 정확 뒤/아닐 때 공격 성공률과 접전(20:20 이후) 랠리 승률
    → player_season_summary  대회별 선수 지표 + 같은 포지션 안의 백분위
    → HDFS /marts/kovo/{테이블}/ (Parquet) 과 PostgreSQL (한 트랜잭션, sql/merge_marts.sql)
    → HDFS /marts/kovo/match_elo/  모든 경기(예정 경기 포함)의 경기 전 Elo와 선수 Elo 팀 전력 (build_features.py의 입력)

매번 모든 시즌을 처음부터 다시 계산한다(몇 초면 끝나고, Elo는 전체 역사에 따라 달라지므로).

Elo 규칙: 시작 1500점. K = 28 × (1, 1.25, 1.5) — 세트 차가 1·2·3일 때. 홈 이점은 전체 경기의 홈 승률에서 구한다.
새 시즌 첫 경기 때 점수를 1500 쪽으로 절반 되돌린다(선수 이동 등으로 전력이 섞이는 것을 반영).
  설정은 2026-11-12 재조정(2011-12~2018-19 시즌으로 고르고 2019-20~2025-26 7시즌, 1,726경기로 시험):
  예전 K 24 · 2/3 유지 대비 경기 전 확률 log loss 0.6414 → 0.6387(경기당 -0.0026, 표준오차 0.0012), 7시즌 중 5시즌 우세.
  조정 시즌에서 K 24~32, 유지 0.4~0.6이 거의 같은 넓은 평지라 한 점에 맞춘 값이 아니다(개선은 주로 유지 비율에서 나온다).
  (Elo: 이길 확률을 점수 차로 예측하고, 결과가 예측보다 좋으면 오르고 나쁘면 내리는 평가 방식)

선수 Elo(예측 특징 pelo_*, 화면에는 쓰지 않음): 선수마다 점수를 두고, 경기 전 팀 전력 = 그 팀의 '예상 출전 명단'
(앞선 경기들의 세트 출전 비율을 지수 이동 평균한 것, 이번 경기의 실제 출전은 절대 쓰지 않음) 점수의 가중 평균.
경기 뒤에는 뛴 선수마다 (팀 Elo와 같은 식의 변화량) × (자기 출전 세트 / 경기 세트 수)만큼 움직인다. 이적하면 점수가
선수를 따라가므로 팀 전력이 바로 바뀐다. 새 시즌에는 각 선수를 1500이 아니라 '앞 두 시즌 중 가장 최근 능력치 카드'
쪽으로 절반 되돌린다(카드 종합 60 = 1500, 1점 = 12 Elo). 처음 보는 선수는 1500에서 시작한다.

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh build_marts.py (평소에는 kovo_daily 등 DAG)
"""
import json
import math
import os
from pathlib import Path

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_marts.sql"
FINISHED = ["FINISHED", "CONFIRMED"]  # 끝난 경기 상태(CONFIRMED = 품질 검사까지 통과)
REGULAR = "201"  # 정규리그 리그 코드

ELO_START, ELO_K, SEASON_CARRY = 1500.0, 28.0, 0.5  # 시작 점수, 기본 K, 새 시즌에 남기는 비율
# 선수 Elo 설정. 2026-11-12 실험에서 2011-12~2018-19 시즌으로 고르고 2019-20~2025-26 7시즌(1,726경기)으로 시험했다:
# 경기 전 확률의 log loss 0.6297 (팀 Elo 0.6414, 팀 Elo를 같은 방식으로 다시 조정해도 0.6387), 7시즌 모두 우세,
# 재조정 팀 Elo보다 경기당 -0.0091 (표준오차 0.0023). 신인·외국인 포지션별 출발 보정과 팀 Elo 섞기는 효과가 없어 뺐다.
PELO_K = 36.0          # 기본 K(세트 차 보정은 팀 Elo와 같다)
PELO_CARRY = 0.5       # 새 시즌에 남기는 비율(나머지는 카드 쪽으로)
PELO_ALPHA = 0.7       # 예상 출전 명단의 이동 평균에서 가장 최근 경기의 비중
PELO_CARD_SCALE = 12.0  # 카드 종합 1점 = Elo 12점 (60 = 1500)
PELO_DROP = 0.02       # 예상 출전 비중이 이보다 작아지면 명단에서 뺀다
CARD_SEASONS = 2       # 앞 몇 시즌의 카드까지 쓰는가(이번 시즌 카드는 쓰지 않는다: 이번 경기 결과가 들어 있다)
MIN_SET_SHARE = 1 / 3  # 순위 자격: 팀 세트의 1/3 이상 출전(선수 순위와 같은 규칙, backend/league/standings.py)
# 지표 → 높을수록 좋은가? (범실은 낮을수록 좋음). 백분위를 매길 방향을 정한다
INDICATORS = {"attack_success_rate": True, "attack_efficiency": True, "receive_efficiency": True,
              "block_per_set": True, "serve_per_set": True, "dig_per_set": True, "set_per_set": True,
              "error_per_set": False}


def read(spark, name):
    """정제 테이블 하나를 읽는다."""
    return spark.read.parquet(f"{CURATED}/{name}")


# ---------------------------------------------------------------- Elo (경기 순서대로 계산해야 해서 드라이버에서 실행)
def home_advantage(rows) -> float:
    """홈 승률을 Elo 점수 차로 환산한 홈 이점(예: 홈 승률 55% ≈ +35점). 팀 Elo와 선수 Elo가 같은 값을 쓴다."""
    home_rate = sum(r.home_sets > r.away_sets for r in rows) / max(1, len(rows))
    return -400 * math.log10(1 / home_rate - 1) if 0 < home_rate < 1 else 0.0


def elo(matches, upcoming=()):
    """Elo를 경기 순서대로 갱신한다.
    matches: 시간순으로 정렬된 끝난 경기 행(season_code, game_no, gender, home_code, away_code, home_sets,
    away_sets, game_date). upcoming: 예정 경기 행(season_code, game_no, home_code, away_code).
    돌려주는 값: (점수 행들, 경기 전 행들, 요약 딕셔너리). '경기 전 행'은 각 경기를 치르기 *전*의 두 팀 점수다
    (예정 경기는 지금 점수). 다음 단계(예측 특징)에서 결과를 미리 보지 않은 값으로 쓰인다."""
    rows = list(matches)
    home_rate = sum(r.home_sets > r.away_sets for r in rows) / max(1, len(rows))
    home_adv = home_advantage(rows)
    rating, season_of = {}, {}  # 팀 → 지금 점수, 팀 → 그 점수가 속한 시즌
    out, pre, hits, log_loss = [], [], 0, 0.0

    def current(team, season):
        """팀이 `season` 경기에 들고 들어가는 점수(새 시즌이면 평균 쪽으로 되돌린 값). 처음 보는 팀은 1500."""
        if team not in rating:
            return ELO_START
        if season_of[team] != season:
            return ELO_START + (rating[team] - ELO_START) * SEASON_CARRY
        return rating[team]

    for r in rows:
        for t in (r.home_code, r.away_code):
            rating[t] = current(t, r.season_code)
            season_of[t] = r.season_code
        rh, ra = rating[r.home_code], rating[r.away_code]
        p_home = 1 / (1 + 10 ** ((ra - rh - home_adv) / 400))  # Elo 공식: 홈이 이길 확률
        pre.append((r.season_code, r.game_no, round(rh, 2), round(ra, 2), round(p_home, 4)))
        home_won = r.home_sets > r.away_sets
        hits += (p_home >= 0.5) == home_won
        log_loss -= math.log(p_home if home_won else 1 - p_home)
        k = ELO_K * (1 + 0.25 * (abs(r.home_sets - r.away_sets) - 1))  # 세트 차가 클수록 크게 움직인다
        change = k * ((1.0 if home_won else 0.0) - p_home)  # (실제 결과 - 예상) × K, 홈이 얻은 만큼 원정이 잃는다
        rating[r.home_code] = rh + change
        rating[r.away_code] = ra - change
        for team, new, delta, p in ((r.home_code, rh + change, change, p_home), (r.away_code, ra - change, -change, 1 - p_home)):
            out.append((r.season_code, r.game_no, team, r.game_date, round(new, 2), round(delta, 2), round(p, 4)))
    for u in upcoming:  # 아직 안 치른 경기: 지금 점수만 기록하고 아무것도 갱신하지 않는다
        rh, ra = current(u.home_code, u.season_code), current(u.away_code, u.season_code)
        pre.append((u.season_code, u.game_no, round(rh, 2), round(ra, 2),
                    round(1 / (1 + 10 ** ((ra - rh - home_adv) / 400)), 4)))
    n = max(1, len(rows))
    return out, pre, {"matches": len(rows), "home_win_rate": round(home_rate, 4), "home_advantage_elo": round(home_adv, 1),
                 "elo_accuracy": round(hits / n, 4), "elo_log_loss": round(log_loss / n, 4)}


def player_elo(rows, lines, cards, upcoming=()):
    """선수 Elo로 모든 경기의 경기 전 팀 전력을 계산한다(머리말의 규칙).
    rows: 시간순 끝난 경기(season_code, game_no, home_code, away_code, home_sets, away_sets).
    lines: {(시즌, 경기 번호): [(팀 코드, 선수 코드, 출전 세트 수)]} — 출전 세트가 0인 줄은 없어도 된다.
    cards: {선수 코드: {시즌: 카드 종합}}. upcoming: 예정 경기(season_code, game_no, home_code, away_code).
    돌려주는 값: ({(시즌, 경기 번호): (홈 전력, 원정 전력, 홈 승리 확률)}, 요약 딕셔너리)."""
    home_adv = home_advantage(rows)
    rating, season_of = {}, {}  # 선수 → 점수, 그 점수가 속한 시즌
    lineup = {}  # 팀 → {선수: 예상 출전 비중}
    pre, log_loss, hits = {}, 0.0, 0

    def target(player, season):
        """새 시즌에 되돌아갈 점수: 앞 CARD_SEASONS 시즌 중 가장 최근 카드, 없으면 1500."""
        for back in range(1, CARD_SEASONS + 1):
            card = cards.get(player, {}).get(f"{int(season) - back:03d}")
            if card is not None:
                return ELO_START + PELO_CARD_SCALE * (card - 60)
        return ELO_START

    def current(player, season):
        """선수가 `season` 경기에 들고 들어가는 점수(새 시즌이면 카드 쪽으로 되돌림). 처음 보는 선수는 1500."""
        if player not in rating:
            rating[player], season_of[player] = ELO_START, season
        elif season_of[player] != season:
            t = target(player, season)
            rating[player] = t + (rating[player] - t) * PELO_CARRY
            season_of[player] = season
        return rating[player]

    def strength(team, season):
        """예상 출전 명단 점수의 가중 평균. 아직 명단이 없는 팀(리그 첫 경기)은 1500."""
        squad = lineup.get(team) or {}
        total = sum(squad.values())
        return sum(w * current(p, season) for p, w in squad.items()) / total if total else ELO_START

    for r in rows:
        key = (r.season_code, r.game_no)
        sh, sa = strength(r.home_code, r.season_code), strength(r.away_code, r.season_code)
        p_home = 1 / (1 + 10 ** ((sa - sh - home_adv) / 400))
        pre[key] = (round(sh, 2), round(sa, 2), round(p_home, 4))
        home_won = r.home_sets > r.away_sets
        hits += (p_home >= 0.5) == home_won
        log_loss -= math.log(p_home if home_won else 1 - p_home)
        change = PELO_K * (1 + 0.25 * (abs(r.home_sets - r.away_sets) - 1)) * ((1.0 if home_won else 0.0) - p_home)
        match_sets = r.home_sets + r.away_sets
        played = {r.home_code: {}, r.away_code: {}}
        for team, player, sets in lines.get(key, ()):
            if sets <= 0 or team not in played:
                continue
            current(player, r.season_code)
            rating[player] += (change if team == r.home_code else -change) * sets / match_sets
            played[team][player] = sets / match_sets
        for team, shares in played.items():
            if not shares:  # 선수 기록이 없는 경기(옛 몰수 경기 등): 명단을 그대로 둔다
                continue
            squad = lineup.setdefault(team, {})
            for player in set(squad) | set(shares):
                squad[player] = (1 - PELO_ALPHA) * squad.get(player, 0.0) + PELO_ALPHA * shares.get(player, 0.0)
            for player in [p for p, w in squad.items() if w < PELO_DROP]:
                del squad[player]
    for u in upcoming:  # 예정 경기: 지금 명단과 점수로 계산만 하고 아무것도 갱신하지 않는다
        sh, sa = strength(u.home_code, u.season_code), strength(u.away_code, u.season_code)
        pre[(u.season_code, u.game_no)] = (round(sh, 2), round(sa, 2), round(1 / (1 + 10 ** ((sa - sh - home_adv) / 400)), 4))
    n = max(1, len(rows))
    return pre, {"players": len(rating), "pelo_accuracy": round(hits / n, 4), "pelo_log_loss": round(log_loss / n, 4)}


# ---------------------------------------------------------------- 사이드아웃 / 브레이크 (ANL-11)
# 사이드아웃 = 상대 서브를 받은 랠리를 이기는 것, 브레이크 = 내가 서브한 랠리를 이기는 것
def serve_receive(rally, matches):
    """(시즌, 리그, 팀)마다: 서브한 랠리 수 / 그중 이긴 수(breaks), 리시브한 랠리 수 / 그중 이긴 수(sideouts).
    랠리의 서버 = 그 랠리 첫 서브 이벤트의 팀, 승자 = +1 이벤트의 팀."""
    first_serve = (rally.where(F.col("action_code").isin("VV", "VS"))
                   .groupBy("season_code", "game_no", "set_no", "rally_no")
                   .agg(F.min_by("team_code", "event_no").alias("server")))
    winner = (rally.where(F.col("grade") > 0).groupBy("season_code", "game_no", "set_no", "rally_no")
              .agg(F.first("team_code").alias("winner")))
    rallies = first_serve.join(winner, ["season_code", "game_no", "set_no", "rally_no"]) \
        .join(matches.select("season_code", "game_no", "league_code", "home_code", "away_code"), ["season_code", "game_no"])
    sides = rallies.select("season_code", "league_code", "server", "winner",
                           F.explode(F.array("home_code", "away_code")).alias("team_code"))
    serving = F.col("server") == F.col("team_code")
    won = F.col("winner") == F.col("team_code")
    return sides.groupBy("season_code", "league_code", "team_code").agg(
        F.sum(F.when(serving, 1).otherwise(0)).alias("served"),
        F.sum(F.when(serving & won, 1).otherwise(0)).alias("breaks"),
        F.sum(F.when(~serving, 1).otherwise(0)).alias("received"),
        F.sum(F.when(~serving & won, 1).otherwise(0)).alias("sideouts"))


ATTACKS = ["AA", "AC", "AT", "AE", "AQ", "AW", "AZ"]  # 공격 동작 코드들


def rally_situations(rally, matches):
    """(시즌, 리그, 팀)마다 문자중계로 계산하는 상황별 효율(ANL-11):
      리시브 직후 첫 공격: 리시브가 정확(RR exc)했을 때와 아닐 때 각각의 공격 성공률
      접전 랠리: 20:20 이후(5세트는 10:10 이후)에 시작한 랠리 수와 그중 이긴 비율"""
    keys = ["season_code", "game_no", "set_no", "rally_no"]
    plays = rally.where((F.col("rally_no") > 0) & (F.col("rally_no") < 200))  # 0 = 세트 시작, 200 = 세트 끝 표시
    # 팀의 리시브(RR) 결과와 그 이벤트 위치 → 그 뒤 같은 팀의 첫 공격이 득점(suc)했는지
    rec = (plays.where(F.col("action_code") == "RR").groupBy(*keys, "team_code")
           .agg(F.min_by("result_code", "event_no").alias("rec"), F.min("event_no").alias("rec_no")))
    att = plays.where(F.col("action_code").isin(ATTACKS)).select(*keys, "team_code", F.col("event_no").alias("att_no"),
                                                                  (F.col("result_code") == "suc").cast("int").alias("kill"))
    first_att = (rec.join(att, [*keys, "team_code"]).where(F.col("att_no") > F.col("rec_no"))
                 .groupBy(*keys, "team_code", "rec").agg(F.min_by("kill", "att_no").alias("kill")))
    after = first_att.groupBy("season_code", "game_no", "team_code").agg(
        F.sum((F.col("rec") == "exc").cast("int")).alias("good_n"),
        F.sum(F.when(F.col("rec") == "exc", F.col("kill")).otherwise(0)).alias("good_kill"),
        F.sum((F.col("rec") != "exc").cast("int")).alias("poor_n"),
        F.sum(F.when(F.col("rec") != "exc", F.col("kill")).otherwise(0)).alias("poor_kill"))

    # 랠리마다 끝난 뒤 점수와 승자 → 직전 랠리 끝 점수(= 이 랠리 시작 점수)로 접전 여부 판단
    rallies = plays.groupBy(*keys).agg(
        F.max("home_score").alias("h_end"), F.max("away_score").alias("a_end"),
        F.first(F.when(F.col("grade") > 0, F.col("team_code")), ignorenulls=True).alias("winner")) \
        .where(F.col("winner").isNotNull())
    w = Window.partitionBy("season_code", "game_no", "set_no").orderBy("rally_no")
    rallies = rallies.withColumn("h0", F.coalesce(F.lag("h_end").over(w), F.lit(0))) \
        .withColumn("a0", F.coalesce(F.lag("a_end").over(w), F.lit(0)))
    line = F.when(F.col("set_no") == 5, 10).otherwise(20)
    clutch = rallies.where((F.col("h0") >= line) & (F.col("a0") >= line))
    m = matches.select("season_code", "game_no", "league_code", "home_code", "away_code")
    clutch_team = clutch.join(m, ["season_code", "game_no"]).select(
        "season_code", "game_no", "winner", F.explode(F.array("home_code", "away_code")).alias("team_code")) \
        .groupBy("season_code", "game_no", "team_code").agg(
            F.count("*").alias("clutch_n"), F.sum((F.col("winner") == F.col("team_code")).cast("int")).alias("clutch_won"))

    per_match = after.join(clutch_team, ["season_code", "game_no", "team_code"], "full") \
        .join(m.select("season_code", "game_no", "league_code"), ["season_code", "game_no"])
    sums = per_match.groupBy("season_code", "league_code", "team_code").agg(
        *[F.sum(F.coalesce(c, F.lit(0))).alias(c) for c in ("good_n", "good_kill", "poor_n", "poor_kill", "clutch_n", "clutch_won")])
    pct = lambda a, b: F.when(F.col(b) > 0, F.round(100 * F.col(a) / F.col(b), 2))  # noqa: E731
    return sums.select("season_code", "league_code", "team_code",
                       pct("good_kill", "good_n").alias("attack_after_good_receive"),
                       pct("poor_kill", "poor_n").alias("attack_after_poor_receive"),
                       F.when(F.col("clutch_n") > 0, F.col("clutch_n")).alias("clutch_rallies"),
                       pct("clutch_won", "clutch_n").alias("clutch_win_rate"))


# ---------------------------------------------------------------- 지표
def rate_columns(prefix=""):
    """합계 컬럼들로 비율 지표 컬럼 식을 만든다(팀·선수 공용). 백분율은 소수 둘째 자리, 세트당은 셋째 자리.
    공격 효율 = (성공 - 막힘 - 범실) / 시도, 리시브 효율 = (정확 - 실패) / 시도."""
    c = lambda name: F.col(prefix + name)  # noqa: E731
    pct = lambda a, b: F.when(b > 0, F.round(100 * a / b, 2))  # noqa: E731
    per_set = lambda a: F.when(c("sets") > 0, F.round(a / c("sets"), 3))  # noqa: E731
    return [
        pct(c("attack_success"), c("attack_attempts")).alias("attack_success_rate"),
        pct(c("attack_success") - c("attack_blocked") - c("attack_errors"), c("attack_attempts")).alias("attack_efficiency"),
        pct(c("receive_excellent") - c("receive_errors"), c("receive_attempts")).alias("receive_efficiency"),
        per_set(c("block_success")).alias("block_per_set"),
        per_set(c("serve_aces")).alias("serve_per_set"),
        per_set(c("dig_success")).alias("dig_per_set"),
        per_set(c("set_success")).alias("set_per_set"),
        per_set(c("errors")).alias("error_per_set"),
    ]


# 경기별 기록에서 더해 갈 항목들
SUMS = ["attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_aces", "block_success",
        "receive_attempts", "receive_excellent", "receive_errors", "dig_success", "set_success", "errors"]


def team_summaries(matches, sets, team_stats, ratings, sr, situations):
    """team_season_summary 행: 승패·승점·세트·점수, 정규리그 순위, 비율 지표, 시즌 마지막 Elo,
    사이드아웃/브레이크율, 상황별 효율을 (시즌, 리그, 팀)마다 하나로 합친다."""
    m = matches.select("season_code", "game_no", "league_code", "home_code", "away_code", "home_sets", "away_sets")
    pts = sets.groupBy("season_code", "game_no").agg(F.sum("home_points").alias("hp"), F.sum("away_points").alias("ap"))
    m = m.join(pts, ["season_code", "game_no"], "left")
    sides = m.select("season_code", "league_code", "game_no",
                     F.explode(F.array(
                         F.struct(F.col("home_code").alias("team_code"), F.col("home_sets").alias("ws"), F.col("away_sets").alias("ls"),
                                  F.col("hp").alias("wp"), F.col("ap").alias("lp")),
                         F.struct(F.col("away_code").alias("team_code"), F.col("away_sets").alias("ws"), F.col("home_sets").alias("ls"),
                                  F.col("ap").alias("wp"), F.col("hp").alias("lp")))).alias("s")).select(
        "season_code", "league_code", "game_no", "s.*")
    # V-리그 승점: 3:0·3:1 승 3점, 3:2 승 2점, 2:3 패 1점, 그 외 패 0점
    win_points = (F.when((F.col("ws") == 3) & (F.col("ls") <= 1), 3).when(F.col("ws") == 3, 2)
                  .when((F.col("ws") == 2) & (F.col("ls") == 3), 1).otherwise(0))
    record = sides.groupBy("season_code", "league_code", "team_code").agg(
        F.count("*").alias("matches"), F.sum((F.col("ws") > F.col("ls")).cast("int")).alias("wins"),
        F.sum((F.col("ws") < F.col("ls")).cast("int")).alias("losses"), F.sum(win_points).alias("win_points"),
        F.sum("ws").alias("sets_won"), F.sum("ls").alias("sets_lost"),
        F.sum(F.coalesce("wp", F.lit(0))).alias("points_won"), F.sum(F.coalesce("lp", F.lit(0))).alias("points_lost"))
    # 정규리그 순위(순위 API와 같은 기준: 승점 → 승수 → 세트 득실률 → 점수 득실률. 상대 전적은 적용 안 함)
    order = Window.partitionBy("season_code", "league_code", "gender").orderBy(
        F.desc("win_points"), F.desc("wins"),
        F.desc(F.col("sets_won") / F.greatest(F.col("sets_lost"), F.lit(0.0001))),
        F.desc(F.col("points_won") / F.greatest(F.col("points_lost"), F.lit(0.0001))))
    gender = matches.select("season_code", "league_code", F.explode(F.array("home_code", "away_code")).alias("team_code"),
                            "gender").distinct()  # 준플레이오프 팀은 원정 경기만 했을 수도 있어 양쪽에서 모은다
    record = record.join(gender, ["season_code", "league_code", "team_code"], "left") \
        .withColumn("rank", F.when(F.col("league_code") == REGULAR, F.rank().over(order)))

    box = team_stats.join(m.select("season_code", "game_no", "league_code"), ["season_code", "game_no"]) \
        .groupBy("season_code", "league_code", "team_code").agg(*[F.sum(c).alias(c) for c in SUMS])
    box = box.join(record.select("season_code", "league_code", "team_code",
                                 (F.col("sets_won") + F.col("sets_lost")).alias("sets")), ["season_code", "league_code", "team_code"])
    box = box.select("season_code", "league_code", "team_code", *rate_columns())

    last = Window.partitionBy("season_code", "league_code", "team_code").orderBy(F.desc("rated_on"), F.desc("game_no"))
    final = ratings.join(m.select("season_code", "game_no", "league_code"), ["season_code", "game_no"]) \
        .withColumn("_n", F.row_number().over(last)).where("_n = 1") \
        .select("season_code", "league_code", "team_code", F.col("rating").alias("final_rating"))
    sr = sr.select("season_code", "league_code", "team_code",
                   F.when(F.col("received") > 0, F.round(100 * F.col("sideouts") / F.col("received"), 2)).alias("sideout_rate"),
                   F.when(F.col("served") > 0, F.round(100 * F.col("breaks") / F.col("served"), 2)).alias("break_rate"))
    keys = ["season_code", "league_code", "team_code"]
    return record.drop("gender").join(box, keys, "left").join(sr, keys, "left").join(final, keys, "left") \
        .join(situations, keys, "left")


def player_summaries(matches, player_stats):
    """player_season_summary 행: (시즌, 리그, 성별, 선수, 팀)마다 합계·비율 지표·순위 자격,
    그리고 같은 대회·성별·포지션의 자격 선수 안에서 지표별 백분위(JSON)."""
    m = matches.select("season_code", "game_no", "league_code", "gender", "home_code", "away_code", "home_sets", "away_sets")
    p = player_stats.join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    # 출전 세트 수 = "11100" 같은 문자열에서 '1'의 개수(전체 길이 - '1'을 지운 길이)
    sets_played = F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))
    agg = p.groupBy("season_code", "league_code", "gender", "player_code", "team_code").agg(
        F.count("*").alias("matches"), F.sum(sets_played).alias("sets"), F.sum("points").alias("points"),
        F.mode("position").alias("position"), *[F.sum(c).alias(c) for c in SUMS])
    team_sets = m.select("season_code", "league_code",
                         F.explode(F.array("home_code", "away_code")).alias("team_code"),
                         (F.col("home_sets") + F.col("away_sets")).alias("s")) \
        .groupBy("season_code", "league_code", "team_code").agg(F.sum("s").alias("team_sets"))
    agg = agg.join(team_sets, ["season_code", "league_code", "team_code"], "left") \
        .withColumn("qualified", F.col("sets") >= F.col("team_sets") * MIN_SET_SHARE)
    agg = agg.select("season_code", "league_code", "gender", "player_code", "team_code",
                     F.coalesce("position", F.lit("")).alias("position"), "matches", "sets", "points", "qualified",
                     *rate_columns())
    # 같은 대회·성별·포지션의 자격 선수 안에서 백분위(0~100. 높을수록 좋게: 범실은 반대로 정렬)
    peers = Window.partitionBy("season_code", "league_code", "gender", "position", "qualified")
    pct_cols = []
    for name, higher_better in INDICATORS.items():
        w = peers.orderBy(F.col(name).asc() if higher_better else F.col(name).desc())
        pct_cols.append(F.when(F.col("qualified") & F.col(name).isNotNull(),
                               F.round(100 * F.percent_rank().over(w))).cast("int").alias(f"p_{name}"))
    agg = agg.select("*", *pct_cols)
    json_map = F.to_json(F.map_from_arrays(
        F.array(*[F.lit(n) for n in INDICATORS]), F.array(*[F.col(f"p_{n}") for n in INDICATORS])))
    return agg.withColumn("percentiles", F.when(F.col("qualified"), json_map).otherwise(F.lit("{}"))) \
        .drop(*[f"p_{n}" for n in INDICATORS])


def main():
    """정제 테이블 읽기 → Elo → 팀·선수 요약 → HDFS 마트 저장 → PostgreSQL 병합."""
    from pg_merge import load_and_merge  # 드라이버에서만 쓴다(airflow 컨테이너의 client 모드)

    spark = (SparkSession.builder.appName("build-marts")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    matches = read(spark, "match").where(F.col("status").isin(FINISHED)).cache()
    sets, team_stats = read(spark, "set_score"), read(spark, "team_match_stat")
    player_stats, rally = read(spark, "player_match_stat"), read(spark, "rally_event")

    upcoming = read(spark, "match").where(F.col("status").isin("SCHEDULED", "LIVE")) \
        .select("season_code", "game_no", "home_code", "away_code").collect()
    ordered = matches.select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets", "away_sets",
                             "game_date", "start_time") \
        .orderBy("game_date", F.coalesce("start_time", F.lit("00:00")), "season_code", "game_no").collect()
    rating_rows, pre_rows, info = elo(ordered, upcoming)
    print("elo:", json.dumps(info))
    # 선수 Elo: 경기별 출전 세트(played_sets의 "1" 개수)와 선수 카드(player_ratings.py가 앞선 실행에서 쓴 것)
    lines = {}
    for r in player_stats.select("season_code", "game_no", "team_code", "player_code",
                                 (F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", ""))).alias("sets")) \
            .where("sets > 0").toLocalIterator():
        lines.setdefault((r.season_code, r.game_no), []).append((r.team_code, r.player_code, r.sets))
    cards = {}
    try:
        for r in spark.read.parquet(f"{MARTS}/player_rating").where(F.col("overall").isNotNull()) \
                .select("season_code", "player_code", "overall").toLocalIterator():
            cards.setdefault(r.player_code, {})[r.season_code] = float(r.overall)
    except Exception as exc:  # 처음 설치한 직후처럼 카드가 아직 없으면 모든 선수를 1500 쪽으로 되돌린다
        print("player cards not found, player Elo regresses toward 1500:", exc.__class__.__name__)
    pelo, pelo_info = player_elo(ordered, lines, cards, upcoming)
    print("player elo:", json.dumps(pelo_info))
    pre_rows = [(s, g, h, a, p, *pelo.get((s, g), (None, None, None))) for s, g, h, a, p in pre_rows]
    ratings = spark.createDataFrame(rating_rows, "season_code string, game_no int, team_code string, rated_on string, "
                                                 "rating double, rating_change double, win_prob double").cache()

    tables = {
        "team_rating": ratings,
        "team_season_summary": team_summaries(matches, sets, team_stats, ratings, serve_receive(rally, matches),
                                              rally_situations(rally, matches)),
        "player_season_summary": player_summaries(matches, player_stats),
    }
    tables = {k: v.cache() for k, v in tables.items()}
    print("rows:", {k: v.count() for k, v in tables.items()})
    for name, df in tables.items():
        df.write.mode("overwrite").parquet(f"{MARTS}/{name}")
    # 모든 경기(끝난 경기와 예정 경기)의 경기 전 Elo: build_features.py의 입력. PostgreSQL에는 넣지 않는다
    spark.createDataFrame(pre_rows, "season_code string, game_no int, home_elo double, away_elo double, "
                                    "elo_home_win_prob double, home_pelo double, away_pelo double, "
                                    "pelo_home_win_prob double").write.mode("overwrite").parquet(f"{MARTS}/match_elo")
    # staging 테이블 적재 + sql/merge_marts.sql 실행을 한 트랜잭션으로(pg_merge.py 참고)
    for row in load_and_merge(spark, tables, MERGE_SQL, "merge_marts"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
