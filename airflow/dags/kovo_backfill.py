"""kovo_backfill: collect and curate past seasons (COL-04, PIPE-08). Manual run with parameters.

  seasons         "001-021" or "019,020"  (001 = 2005, 022 = 2025-26)
  limit           at most this many games per season (for a quick test); empty = all
  skip_confirmed  skip games already CONFIRMED in PostgreSQL (a re-run only fetches what is missing)

Each season: schedule -> box scores + play-by-play of finished games -> wait for HDFS -> Spark curation.
After all seasons: analysis marts (Elo over the whole history, season summaries).
Seasons run one after another for KOVO requests (shared 1 req/s limit) and for Spark, but season N+1
can collect while season N is being curated. A full season is about 1,300 requests (~22 min).
"""
from datetime import date, timedelta

import pendulum
from airflow.sdk import Param, dag, get_current_context, task, task_group

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args()


@dag(
    dag_id="kovo_backfill",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "collect", "manual"],
    doc_md=__doc__,
    params={
        "seasons": Param("021", type="string", pattern=r"^\d{1,3}(-\d{1,3})?(,\s*\d{1,3}(-\d{1,3})?)*$",
                         description="e.g. 001-021 or 019,020"),
        "limit": Param(None, type=["null", "integer"], minimum=1, description="games per season (test runs)"),
        "skip_confirmed": Param(True, type="boolean"),
    },
)
def kovo_backfill():
    @task
    def plan() -> list:
        seasons = p.parse_seasons(get_current_context()["params"]["seasons"])
        print(f"{len(seasons)} season(s): {seasons}")
        return seasons

    @task
    def season_list() -> dict:
        batch, current, _ = p.collect_season_list()
        print(f"current season: {current}")
        return batch

    @task_group
    def one_season(season: str, season_batch: dict):
        @task(max_active_tis_per_dag=1)
        def collect(season: str, season_batch: dict) -> dict:
            params = get_current_context()["params"]
            schedule_batch, items = p.collect_schedule(season)
            confirmed = p.confirmed_game_nos(season) if params["skip_confirmed"] else set()
            targets = p.pick_targets(items, confirmed, since=date.min)
            if params["limit"]:
                targets = targets[:params["limit"]]
            print(f"season {season}: {len(items)} games, {len(targets)} to collect")
            return {"season": season, "batches": [season_batch, schedule_batch] + p.collect_games(targets),
                    "games": len(targets)}

        @task.sensor(poke_interval=60, timeout=3600, mode="reschedule")
        def wait_for_raw(result: dict) -> bool:
            return p.raw_arrived(result["batches"])

        @task(max_active_tis_per_dag=1)
        def curate(result: dict):
            p.run_curation([result["season"]])

        result = collect(season, season_batch)
        wait_for_raw(result) >> curate(result)

    # once every season is curated (even if some failed): Elo needs the whole history
    @task(trigger_rule="all_done")
    def build_marts():
        p.run_marts()

    one_season.partial(season_batch=season_list()).expand(season=plan()) >> build_marts()


kovo_backfill()
