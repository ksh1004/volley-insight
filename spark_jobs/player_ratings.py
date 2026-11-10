"""Position ratings for player cards: every player-season scored 1-99 on the abilities that matter for the position,
plus an overall score. Agreed with the user on 2026-11-09 after a data study (numbers below are from it).

  HDFS /curated/kovo/{match,player_match_stat,player_attack_stat}  regular season, all seasons  -> box-score counts
  HDFS /curated/kovo/rally_event (2023-24 on)                                                   -> serve pressure,
                                                                                                   setter's added kills
    -> per gender x position x season: reference = qualified players of that season and the two before
    -> each metric shrunk toward the reference mean (few attempts -> average), then z within the reference
    -> ability = mean z of its metrics, re-standardised; score = 60 + 12 z, clipped to 1..99
       (60 = average qualified player of the position, 72 = +1 sd)
    -> overall = weighted average of the ability scores on the card (mean 60, sd about 8: 80+ is rare)
    -> career card per player and position: each ability = the season scores averaged with the sets played as
       weights (every season is already scored against its own era), the record behind it pooled over the seasons
    -> PostgreSQL player_rating, player_rating_career (full refresh, sql/merge_player_ratings.sql)
    -> HDFS /marts/kovo/player_rating (season, player, position, overall): the match model's squad feature
       (build_features.py uses a player's rating from earlier seasons only)

Abilities and why (study 2026-11-09, "moved" = correlation with last season for players who changed teams, the
strictest test that a number follows the player rather than his team):
  OH  receive (rec. excellent rate, moved 0.55), scoring (points/set 0.51), block (blocks/set 0.70),
      serve (pressure / (aces-errors) per serve 0.55), attack (efficiency: only 0.08, heavily shrunk), defence (digs/set 0.38)
  OP  scoring (0.78), attack (0.52), block (0.70), serve (pressure / aces per set 0.49), defence (0.58)
  MB  block (0.41), touch (effective blocks per block attempt 0.53; per set only 0.18), quick (0.30),
      serve (pressure / aces per set 0.45)
  S   setting, serve, block. 세트 성공 (KOVO set success) = sets the attacker turned into a point, i.e. the
      attackers' number: for setters who moved it followed the new team (0.30) more than the setter (0.15). From
      2023-24 the setting score is the kill rate on his sets minus what the same attackers convert on other setters'
      balls; before that the set success rate is used and flagged as a reference only.
  L   receive (0.51), dig (digs/set + dig rate), linking (set attempts per set: how much of the second ball the
      libero takes; the quality of those sets was not measurable, next-season r 0.02; shown, not weighted)
Not used (mostly luck, next-season r <= 0.3): open attack rate, dig rate alone, libero receive errors.

Weights were then checked against results (2026-11-09): the set-weighted mean overall of a team's qualified
players vs its win rate, 234 team-seasons 2008-09..2025-26. Started at OH 25/20/15/15/15/10, OP 30/25/20/15/10,
MB 35/20/25/20, L 50/35/15: r 0.713 (equal weights 0.663). Ability alone vs win rate: attack 0.73, setting 0.63,
quick 0.55, block 0.48, serve 0.31, receive 0.24, scoring 0.20, dig 0.10, OH/OP defence -0.04, MB touch -0.02,
libero linking -0.18. Moving weight from defence / touch / linking to attack and quick (below) gave r 0.738, better
in both halves of history (0.772 -> 0.795, 0.663 -> 0.686), so it is not fitted to one period.

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh player_ratings.py   (also in kovo_daily)
"""
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
MARTS = os.environ.get("MARTS_PATH", "hdfs://namenode:9000/marts/kovo")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_player_ratings.sql"
FINISHED = ["FINISHED", "CONFIRMED"]
REGULAR = "201"
PBP_FROM = "020"  # play-by-play exists from 2023-24
MIN_SET_SHARE, MIN_SETS, SURE_SETS = 1 / 3, 10, 30
WINDOW = 3  # reference seasons: this one and the two before
MIN_REF = 8  # fewer qualified players in the reference -> no score
SCALE_MEAN, SCALE_SD, LOW, HIGH = 60, 12, 1, 99
ATTACKS = ["AA", "AC", "AT", "AE", "AQ", "AW", "AZ"]
SETTER_PRIOR = 20  # sets: an attacker's baseline is pulled to his own season rate when he barely had other setters
CAREER_MIN_QUALIFIED = 3  # qualified seasons at the position to be ranked all-time (careers: mean 59-60, sd 5-8)
PERIODS = ("2023–24부터", "2022–23까지")  # career record pooled only over play-by-play seasons / only before them

# metric -> (numerator, denominator, prior weight, prior): rates are (made + prior_mean*k) / (attempts + k);
# per-set counts use sets as the denominator; prior "zero" shrinks toward 0 (a difference from expected)
METRICS = {
    "rec_exc": ("receive_excellent", "receive_attempts", 60, "mean"),
    "attack_eff": ("attack_net", "attack_attempts", 80, "mean"),
    "serve_net": ("serve_net", "serve_attempts", 60, "mean"),
    "serve_pressure": ("serve_hits", "serves", 80, "mean"),
    "quick_rate": ("quick_success", "quick_attempts", 40, "mean"),
    "block_touch_rate": ("block_effective", "block_attempts", 60, "mean"),
    "set_rate": ("set_success", "set_attempts", 100, "mean"),
    "setter_added": ("set_added", "pbp_sets", 300, "zero"),
    "dig_rate": ("dig_success", "dig_attempts", 40, "mean"),
    "points_ps": ("points", "sets", 10, "mean"),
    "block_ps": ("block_success", "sets", 10, "mean"),
    "ace_ps": ("serve_aces", "sets", 10, "mean"),
    "dig_ps": ("dig_success", "sets", 10, "mean"),
    "set_att_ps": ("set_attempts", "sets", 10, "mean"),
}

# position -> [(key, label, weight, alternatives)]: an alternative is a list of metrics averaged into the ability;
# the first alternative the player has a value for is used (play-by-play versions first)
SERVE_OH = [["serve_pressure"], ["serve_net"]]
SERVE = [["serve_pressure"], ["ace_ps"]]
ABILITIES = {
    "OH": [("receive", "리시브", 25, [["rec_exc"]]), ("scoring", "득점력", 20, [["points_ps"]]),
           ("block", "블로킹", 15, [["block_ps"]]), ("serve", "서브", 15, SERVE_OH),
           ("attack", "공격", 20, [["attack_eff"]]), ("defense", "수비", 5, [["dig_ps"]])],
    "OP": [("scoring", "득점력", 25, [["points_ps"]]), ("attack", "공격", 30, [["attack_eff"]]),
           ("block", "블로킹", 20, [["block_ps"]]), ("serve", "서브", 15, SERVE), ("defense", "수비", 10, [["dig_ps"]])],
    "MB": [("block", "블로킹", 35, [["block_ps"]]), ("touch", "견제", 10, [["block_touch_rate"]]),
           ("quick", "속공", 35, [["quick_rate"]]), ("serve", "서브", 20, SERVE)],
    "S": [("setting", "토스", 50, [["setter_added"], ["set_rate"]]), ("serve", "서브", 25, SERVE),
          ("block", "블로킹", 25, [["block_ps"]])],
    # 연결 is shown on the card but not counted: more libero sets went with losing (team r -0.18)
    "L": [("receive", "리시브", 60, [["rec_exc"]]), ("dig", "디그", 40, [["dig_ps", "dig_rate"]]),
          ("linking", "연결", 0, [["set_att_ps"]])],
}
# what each metric means on the card (raw, unshrunk value)
SHOW = {
    "rec_exc": ("리시브 정확", "pct"), "attack_eff": ("공격 효율", "pct"), "serve_net": ("서브 (에이스-범실)/시도", "pct"),
    "serve_pressure": ("서브 압박 (에이스·흔든 리시브)", "pct"), "quick_rate": ("속공 성공률", "pct"),
    "block_touch_rate": ("블로킹 시도당 유효 블로킹", "pct"), "set_rate": ("세트 성공률", "pct"),
    "setter_added": ("같은 공격수 대비 득점률", "pp"), "dig_rate": ("디그 성공률", "pct"),
    "points_ps": ("세트당 득점", "n2"), "block_ps": ("세트당 블로킹", "n2"), "ace_ps": ("세트당 서브 에이스", "n2"),
    "dig_ps": ("세트당 디그", "n2"), "set_att_ps": ("세트당 토스 시도", "n2"),
}
NOTES = {"set_rate": "공격수 영향이 큰 참고값 (문자중계가 없는 시즌)"}
CAREER_NOTES = {"set_rate": "세트 성공률로 계산한 시즌은 공격수 영향이 큰 참고값"}  # when mixed with play-by-play seasons


# ---------------------------------------------------------------- pure helpers (tested in spark_jobs/tests)
def to_score(z):
    return None if z is None else max(LOW, min(HIGH, round(SCALE_MEAN + SCALE_SD * z)))


def raw_value(row, metric):
    num, den, _, _ = METRICS[metric]
    d = row.get(den)
    return None if not d or row.get(num) is None else row[num] / d


def shrunk_value(row, metric, prior_mean):
    num, den, k, prior = METRICS[metric]
    d = row.get(den)
    if d is None or row.get(num) is None:
        return None
    centre = 0.0 if prior == "zero" else prior_mean
    return (row[num] + centre * k) / (d + k)


def pooled_mean(rows, metric):
    num, den, _, prior = METRICS[metric]
    if prior == "zero":
        return 0.0
    made = sum(r[num] for r in rows if r.get(den) and r.get(num) is not None)
    att = sum(r[den] for r in rows if r.get(den) and r.get(num) is not None)
    return made / att if att else None


def standardise(values, reference):
    """z of each value against the reference values (population sd); None where there is no value or reference."""
    ref = [v for v in reference if v is not None]
    if len(ref) < MIN_REF:
        return [None] * len(values)
    mu, sd = statistics.mean(ref), statistics.pstdev(ref)
    return [None if v is None or not sd else (v - mu) / sd for v in values]


def score_group(group, targets, position):
    """Score `targets` (rows of one season) against `group` (same gender and position, the reference seasons).
    Rows are dicts of counts; `qualified` marks reference rows. Returns {id(row): result} for the targets."""
    rows = list({id(r): r for r in group + targets}.values())
    in_group = {id(r) for r in group}
    ref = [r for r in rows if r["qualified"] and id(r) in in_group]
    zs = {}  # metric -> {id(row): z}
    for metric in {m for _, _, _, alts in ABILITIES[position] for alt in alts for m in alt}:
        has = [r for r in ref if r.get(METRICS[metric][1])]
        mean = pooled_mean(has, metric)
        if mean is None or len(has) < MIN_REF:
            continue
        vals = [shrunk_value(r, metric, mean) for r in rows]
        refvals = [shrunk_value(r, metric, mean) for r in has]
        zs[metric] = {id(r): z for r, z in zip(rows, standardise(vals, refvals))}

    ability_z = defaultdict(dict)  # ability key -> {id(row): z}
    chosen = defaultdict(dict)  # ability key -> {id(row): metrics used}
    for key, _, _, alts in ABILITIES[position]:
        per_alt = []
        for alt in alts:
            if not all(m in zs for m in alt):
                per_alt.append({})
                continue
            raw = {}
            for r in rows:
                parts = [zs[m].get(id(r)) for m in alt]
                raw[id(r)] = statistics.mean(parts) if all(p is not None for p in parts) else None
            # re-standardise a combined ability against the reference rows that have it
            ids = list(raw)
            z = standardise([raw[i] for i in ids], [raw[id(r)] for r in ref])
            per_alt.append(dict(zip(ids, z)))
        for r in rows:
            for alt, z in zip(alts, per_alt):
                if z.get(id(r)) is not None:
                    ability_z[key][id(r)] = z[id(r)]
                    chosen[key][id(r)] = alt
                    break

    out = {}
    for r in targets:
        abilities = []
        for key, label, weight, _ in ABILITIES[position]:
            z = ability_z[key].get(id(r))
            used = chosen[key].get(id(r), [])
            stats = [{"metric": m, "label": SHOW[m][0], "unit": SHOW[m][1], "value": raw_value(r, m)} for m in used]
            note = next((NOTES[m] for m in used if m in NOTES), "")
            abilities.append({"key": key, "label": label, "weight": weight, "score": to_score(z),
                              "z": None if z is None else round(z, 3), "stats": stats, "note": note})
        out[id(r)] = {"overall": overall_score(abilities), "abilities": abilities}
    return out


def overall_score(abilities):
    """Weighted average of the ability scores shown on the card (agreed 2026-11-09: anyone can check it from the
    card). None when more than one weighted ability has no score. Zero-weight abilities are shown, not counted."""
    weighted = [a for a in abilities if a["weight"]]
    parts = [(a["weight"], a["score"]) for a in weighted if a["score"] is not None]
    if not parts or len(parts) < len(weighted) - 1:
        return None
    return round(sum(w * s for w, s in parts) / sum(w for w, _ in parts))


def career_cards(scored):
    """One career card per player and position from the scored seasons ([(row, {"overall", "abilities"})]).
    Ability = season scores averaged with the sets played as weights (seasons without a score left out); the
    record behind it = each metric pooled over the seasons that used it, with the period when that was only part of
    the career (play-by-play metrics); overall = weighted average of these abilities, as on a season card."""
    by = defaultdict(list)
    for r, s in scored:
        by[(r["player_code"], r["gender"], r["position"])].append((r, s))
    cards = []
    for (player, gender, position), seasons in by.items():
        seasons.sort(key=lambda x: x[0]["season_code"])
        abilities = []
        for i, (key, label, weight, _) in enumerate(ABILITIES[position]):
            parts = [(r, s["abilities"][i]) for r, s in seasons if s["abilities"][i]["score"] is not None]
            sets = sum(r["sets"] for r, _ in parts)
            score = round(sum(r["sets"] * a["score"] for r, a in parts) / sets) if sets else None
            used = {}  # metric -> rows of the seasons that used it, in order of first use
            for r, a in parts:
                for st in a["stats"]:
                    used.setdefault(st["metric"], []).append(r)
            stats = []
            for metric, rows in used.items():
                num, den, _, _ = METRICS[metric]
                rows = [r for r in rows if r.get(den) and r.get(num) is not None]
                total = sum(r[den] for r in rows)
                stat = {"metric": metric, "label": SHOW[metric][0], "unit": SHOW[metric][1],
                        "value": sum(r[num] for r in rows) / total if total else None}
                codes = [r["season_code"] for r in rows]
                if len(codes) < len(parts) and codes:
                    if min(codes) >= PBP_FROM:
                        stat["period"] = PERIODS[0]
                    elif max(codes) < PBP_FROM:
                        stat["period"] = PERIODS[1]
                stats.append(stat)
            periods = {st["metric"]: st.get("period") for st in stats}
            note = next(((CAREER_NOTES if periods[m] else NOTES)[m] for m in used if m in NOTES), "")
            abilities.append({"key": key, "label": label, "weight": weight, "score": score, "z": None,
                              "stats": stats, "note": note})
        rated = [(r, s) for r, s in seasons if s["overall"] is not None]
        best = max([x for x in rated if x[0]["qualified"]] or rated, key=lambda x: x[1]["overall"], default=None)
        qualified_seasons = sum(1 for r, _ in seasons if r["qualified"])
        extra = {}
        if position == "S":
            extra["set_success_ps"] = round(sum(r["set_success"] for r, _ in seasons)
                                            / sum(r["sets"] for r, _ in seasons), 3)
        cards.append({"player_code": player, "gender": gender, "position": position, "seasons": len(seasons),
                      "qualified_seasons": qualified_seasons, "sets": sum(r["sets"] for r, _ in seasons),
                      "first_season": seasons[0][0]["season_code"], "last_season": seasons[-1][0]["season_code"],
                      "best_season": best[0]["season_code"] if best else None,
                      "best_overall": best[1]["overall"] if best else None,
                      "qualified": qualified_seasons >= CAREER_MIN_QUALIFIED,
                      "overall": overall_score(abilities), "abilities": abilities, "extra": extra})
    return cards


def score_all(rows):
    """Every row (player-season) scored against its gender x position x reference seasons."""
    by = defaultdict(list)
    for r in rows:
        if r["position"] in ABILITIES:
            by[(r["gender"], r["position"])].append(r)
    results = []
    for (gender, position), group in by.items():
        seasons = sorted({r["season_code"] for r in group})
        for season in seasons:
            lo = f"{int(season) - WINDOW + 1:03d}"
            ref = [r for r in group if lo <= r["season_code"] <= season]
            targets = [r for r in group if r["season_code"] == season]
            scored = score_group(ref, targets, position)
            for r in targets:
                results.append((r, scored[id(r)]))
    return results


def setter_added(set_rows, setters):
    """Kills on a setter's sets minus what the same attackers converted on other setters' sets that season.
    set_rows: [(season, setter, attacker, sets, kills)]; setters: {(season, player)} whose position is S.
    Returns {(season, setter): (added kills, sets)}. An attacker without other setters falls back toward his own
    season rate, so those attacks add about nothing (nothing can be told apart)."""
    by_attacker = defaultdict(lambda: [0, 0])  # all sets he hit
    by_setter_s = defaultdict(lambda: [0, 0])  # sets from position-S players only
    for season, setter, attacker, n, k in set_rows:
        by_attacker[(season, attacker)][0] += k
        by_attacker[(season, attacker)][1] += n
        if (season, setter) in setters:
            by_setter_s[(season, attacker)][0] += k
            by_setter_s[(season, attacker)][1] += n
    out = defaultdict(lambda: [0.0, 0])
    for season, setter, attacker, n, k in set_rows:
        if (season, setter) not in setters:
            continue
        ak, an = by_attacker[(season, attacker)]
        own_rate = ak / an if an else 0.0
        sk, sn = by_setter_s[(season, attacker)]
        other_k, other_n = sk - k, sn - n
        base = (other_k + own_rate * SETTER_PRIOR) / (other_n + SETTER_PRIOR)
        out[(season, setter)][0] += k - base * n
        out[(season, setter)][1] += n
    return {key: (v[0], v[1]) for key, v in out.items()}


# ---------------------------------------------------------------- Spark: counts per player-season
def player_counts(spark):
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR))
         .select("season_code", "game_no", "gender", "home_code", "away_code", "home_sets", "away_sets"))
    p = read("player_match_stat").join(m, ["season_code", "game_no"]).where(F.col("played_sets") != "00000")
    p = p.withColumn("sets", F.length("played_sets") - F.length(F.regexp_replace("played_sets", "1", "")))

    by_team = p.groupBy("season_code", "player_code", "team_code").agg(F.sum("sets").alias("team_sets_played"))
    w = Window.partitionBy("season_code", "player_code").orderBy(F.desc("team_sets_played"), "team_code")
    main_team = by_team.withColumn("_n", F.row_number().over(w)).where("_n = 1").select("season_code", "player_code", "team_code")
    sums = ["points", "attack_attempts", "attack_success", "attack_blocked", "attack_errors", "serve_attempts",
            "serve_aces", "serve_errors", "block_attempts", "block_success", "block_effective", "receive_attempts",
            "receive_excellent", "dig_attempts", "dig_success", "set_attempts", "set_success"]
    agg = p.groupBy("season_code", "player_code", "gender").agg(
        F.sum("sets").alias("sets"), F.mode(F.when(F.col("position") != "", F.col("position"))).alias("position"),
        *[F.sum(c).alias(c) for c in sums])
    team_sets = m.select("season_code", F.explode(F.array("home_code", "away_code")).alias("team_code"),
                         (F.col("home_sets") + F.col("away_sets")).alias("s")) \
        .groupBy("season_code", "team_code").agg(F.sum("s").alias("team_sets"))
    agg = agg.join(main_team, ["season_code", "player_code"]).join(team_sets, ["season_code", "team_code"]) \
        .where(F.col("sets") >= MIN_SETS) \
        .withColumn("qualified", F.col("sets") >= F.col("team_sets") * MIN_SET_SHARE)

    quick = (read("player_attack_stat").join(m.select("season_code", "game_no"), ["season_code", "game_no"])
             .where(F.col("attack_type").isin("QUICK", "MOVING"))
             .groupBy("season_code", "player_code")
             .agg(F.sum("attempts").alias("quick_attempts"), F.sum("success").alias("quick_success")))
    return agg.join(quick, ["season_code", "player_code"], "left")


def play_by_play(spark):
    """Per player-season from play-by-play: serves and serve hits (ace, or the reception was not 'excellent');
    setter -> attacker pairs with kills."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    m = (read("match").where(F.col("status").isin(FINISHED) & (F.col("league_code") == REGULAR)
                             & (F.col("season_code") >= PBP_FROM)).select("season_code", "game_no"))
    e = read("rally_event").join(m, ["season_code", "game_no"])
    w = Window.partitionBy("season_code", "game_no", "set_no").orderBy("event_no")
    e = e.select("season_code", "team_code", "player_code", "action_code", "result_code",
                 F.lead("action_code").over(w).alias("na"), F.lead("result_code").over(w).alias("nr"),
                 F.lead("team_code").over(w).alias("nt"), F.lead("player_code").over(w).alias("np"))
    serves = (e.where(F.col("action_code").isin("VV", "VS") & F.col("player_code").isNotNull())
              .withColumn("hit", ((F.col("result_code") == "suc")
                                  | ((F.col("na") == "RR") & (F.col("nt") != F.col("team_code")) & (F.col("nr") != "exc")))
                          .cast("int"))
              .groupBy("season_code", "player_code").agg(F.count("*").alias("serves"), F.sum("hit").alias("serve_hits")))
    sets = (e.where((F.col("action_code") == "SS") & F.col("na").isin(ATTACKS) & (F.col("nt") == F.col("team_code"))
                    & F.col("player_code").isNotNull() & F.col("np").isNotNull())
            .groupBy("season_code", F.col("player_code").alias("setter"), F.col("np").alias("attacker"))
            .agg(F.count("*").alias("n"), F.sum((F.col("nr") == "suc").cast("int")).alias("kills")))
    return serves, sets


def build_rows(counts, serves, set_pairs):
    from player_types import normalize_position

    serve_by = {(r.season_code, r.player_code): (r.serves, r.serve_hits) for r in serves}
    rows = []
    for r in counts:
        d = r.asDict()
        d["position"] = normalize_position(d["season_code"], d["position"] or "")
        d["attack_net"] = d["attack_success"] - d["attack_blocked"] - d["attack_errors"]
        d["serve_net"] = d["serve_aces"] - d["serve_errors"]
        for c in ("quick_attempts", "quick_success"):
            d[c] = d[c] or 0
        sv = serve_by.get((d["season_code"], d["player_code"]))
        d["serves"], d["serve_hits"] = sv if sv and sv[0] >= 1 else (None, None)
        rows.append(d)
    setters = {(r["season_code"], r["player_code"]) for r in rows if r["position"] == "S"}
    added = setter_added([(s.season_code, s.setter, s.attacker, s.n, s.kills) for s in set_pairs], setters)
    for r in rows:
        a = added.get((r["season_code"], r["player_code"]))
        r["set_added"], r["pbp_sets"] = a if a else (None, None)
    return rows


def main():
    from pyspark.sql import SparkSession

    from pg_merge import load_and_merge

    spark = (SparkSession.builder.appName("player-ratings")
             .config("spark.sql.session.timeZone", "Asia/Seoul")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    counts = player_counts(spark).collect()
    serves, set_pairs = play_by_play(spark)
    rows = build_rows(counts, serves.collect(), set_pairs.collect())
    scored = score_all(rows)
    out = []
    for r, s in scored:
        extra = {"set_success_ps": round(r["set_success"] / r["sets"], 3)} if r["position"] == "S" else {}
        out.append((r["season_code"], r["player_code"], r["team_code"], r["gender"], r["position"], int(r["sets"]),
                    bool(r["qualified"]), (not r["qualified"]) or r["sets"] < SURE_SETS, s["overall"],
                    json.dumps(s["abilities"], ensure_ascii=False), json.dumps(extra)))
    print(f"scored {len(out)} player-seasons")
    for pos in ABILITIES:
        n = sum(1 for o in out if o[4] == pos and o[8] is not None)
        print(f"  {pos}: {n} with an overall score")
    df = spark.createDataFrame(out, "season_code string, player_code string, team_code string, gender string, "
                                    "position string, sets int, qualified boolean, provisional boolean, overall int, "
                                    "abilities string, extra string")
    careers = [(c["player_code"], c["gender"], c["position"], c["seasons"], c["qualified_seasons"], int(c["sets"]),
                c["first_season"], c["last_season"], c["best_season"], c["best_overall"], c["qualified"], c["overall"],
                json.dumps(c["abilities"], ensure_ascii=False), json.dumps(c["extra"])) for c in career_cards(scored)]
    print(f"career cards {len(careers)} ({sum(c[10] for c in careers)} ranked all-time)")
    career_df = spark.createDataFrame(careers, "player_code string, gender string, position string, seasons int, "
                                               "qualified_seasons int, sets int, first_season string, "
                                               "last_season string, best_season string, best_overall int, "
                                               "qualified boolean, overall int, abilities string, extra string")
    df.select("season_code", "player_code", "team_code", "gender", "position", "sets", "qualified", "overall") \
        .write.mode("overwrite").parquet(f"{MARTS}/player_rating")
    tables = {"player_rating": df, "player_rating_career": career_df}
    for row in load_and_merge(spark, tables, MERGE_SQL, "merge_player_ratings"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
