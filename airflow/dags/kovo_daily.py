"""kovo_daily: refresh the current season every morning (COL-01, COL-02, PIPE-04).

06:00 every day:
  1. season list -> the current season (latest season code with game leagues)
  2. that season's schedule and results
  3. catch-up: finished games of the last 7 days that are not CONFIRMED (e.g. kovo_postgame did not run)
  4. wait until raw-archiver has written every message to HDFS /raw/
  5. Spark curation of the season
  6. analysis marts: Elo ratings, team/player season summaries (all seasons)
  7. Elasticsearch `entities` (team/player search and autocomplete), rebuilt from the curated data
  8. player types (K-means) and similar players, all seasons (ANL-06): player_type, player_style
  9. quality gate (PIPE-10): fails when a game of the last 7 days has an OPEN error issue (e.g. missing
     play-by-play after re-collection) or a regular-season game has no result 6 hours after its start.
     Known gaps in KOVO's own data are acknowledged in Django admin and no longer fail the run.
Seasons with a match-level correction (ADM-03) changed in the last day are curated again with the current one.
Not yet: standings and player profiles (needs new collectors).
"""
from datetime import timedelta

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args()
CATCH_UP_DAYS = 7


@dag(
    dag_id="kovo_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "collect"],
    doc_md=__doc__,
)
def kovo_daily():
    @task
    def collect() -> dict:
        season_batch, season, leagues = p.collect_season_list()
        schedule_batch, items = p.collect_schedule(season, leagues)
        since = p.now_kst().date() - timedelta(days=CATCH_UP_DAYS)
        targets = p.pick_targets(items, p.confirmed_game_nos(season), since=since)
        print(f"season {season}: {len(items)} games in schedule, {len(targets)} finished game(s) to collect")
        return {"season": season, "batches": [season_batch, schedule_batch] + p.collect_games(targets),
                "games": [g.key for g in targets]}

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        return p.raw_arrived(result["batches"])

    @task
    def curate(result: dict):
        corrected = p.corrected_seasons(p.now_kst() - timedelta(days=1))
        if corrected:
            print("seasons with new corrections:", ", ".join(corrected))
        p.run_curation([result["season"], *corrected])

    @task
    def build_marts():
        p.run_marts()

    @task
    def index_entities():
        p.run_index_entities()

    @task
    def player_types():
        p.run_player_types()

    @task(retries=0)
    def quality_gate():
        now = p.now_kst()
        report = p.quality_report(now - timedelta(days=CATCH_UP_DAYS), now)
        print("open issues of all seasons:", report["open"] or "none")
        problems = p.gate_problems(report)
        if problems:
            raise AirflowFailException("data quality: " + "; ".join(problems)
                                       + " (fix, or acknowledge a source gap in Django admin > 품질 검사 결과)")
        print(f"quality gate passed: no open error in the last {CATCH_UP_DAYS} days")

    @task
    def report(result: dict):
        since = p.now_kst().date() - timedelta(days=CATCH_UP_DAYS)
        rows = p.match_report(result["season"], since)
        not_confirmed = [f"{result['season']}-{g} ({s})" for g, _, s in rows if s != "CONFIRMED"]
        print(f"last {CATCH_UP_DAYS} days: {len(rows)} game(s), not confirmed: {not_confirmed or 'none'}")
        print("collected:", ", ".join(result["games"]) or "none")

    result = collect()
    marts = build_marts()
    curated = curate(result)
    wait_for_raw(result) >> curated >> marts >> report(result)
    curated >> quality_gate()
    marts >> index_entities()
    marts >> player_types()


kovo_daily()
