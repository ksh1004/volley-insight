"""kovo_daily: refresh the current season every morning (COL-01, COL-02, PIPE-04).

06:00 every day:
  1. season list -> the current season (latest season code with game leagues)
  2. that season's schedule and results
  3. catch-up: finished games of the last 7 days that are not CONFIRMED (e.g. kovo_postgame did not run)
  4. wait until raw-archiver has written every message to HDFS /raw/
  5. Spark curation of the season
  6. analysis marts: Elo ratings, team/player season summaries (all seasons)
Not yet: standings and player profiles (needs new collectors), Elasticsearch `entities`.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

from kovo_tasks import pipeline as p

DEFAULT_ARGS = {"retries": 3, "retry_delay": timedelta(minutes=5)}
CATCH_UP_DAYS = 7


@dag(
    dag_id="kovo_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
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
        p.run_curation([result["season"]])

    @task
    def build_marts():
        p.run_marts()

    @task
    def report(result: dict):
        since = p.now_kst().date() - timedelta(days=CATCH_UP_DAYS)
        rows = p.match_report(result["season"], since)
        not_confirmed = [f"{result['season']}-{g} ({s})" for g, _, s in rows if s != "CONFIRMED"]
        print(f"last {CATCH_UP_DAYS} days: {len(rows)} game(s), not confirmed: {not_confirmed or 'none'}")
        print("collected:", ", ".join(result["games"]) or "none")

    result = collect()
    wait_for_raw(result) >> curate(result) >> build_marts() >> report(result)


kovo_daily()
