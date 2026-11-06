"""kovo_postgame: box scores and play-by-play of games that just ended (COL-03, COL-10, NFR-13).

Every 20 minutes from 14:00 to 23:40 (weekend games start at 14:00/16:00, weekday games at 19:00):
  1. games to watch: started, not CONFIRMED games since yesterday, from PostgreSQL. None -> skip,
     so non-game days cost no KOVO request.
  2. refresh that season's schedule (results come from it), pick the games that have finished,
     collect their box scores and play-by-play. Nothing finished yet -> skip.
  3. wait until raw-archiver has written every message to HDFS /raw/
  4. Spark curation of the season -> the games become CONFIRMED (or stay FINISHED if a check fails,
     e.g. KOVO has not published the play-by-play yet; the next run collects them again).
  5. analysis marts (Elo after the new results, season summaries)
"""
from datetime import timedelta

import pendulum
from airflow.exceptions import AirflowSkipException
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args()


@dag(
    dag_id="kovo_postgame",
    schedule="*/20 14-23 * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "collect"],
    doc_md=__doc__,
)
def kovo_postgame():
    @task
    def find_games_to_watch() -> dict:
        watch = p.games_to_watch(p.now_kst())
        if not watch:
            raise AirflowSkipException("no started, unconfirmed game since yesterday")
        return watch

    @task
    def collect_finished(watch: dict) -> dict:
        since = p.now_kst().date() - timedelta(days=1)
        batches, seasons, keys = [], [], []
        for season, game_nos in watch.items():
            batch, items = p.collect_schedule(season)
            batches.append(batch)
            targets = p.pick_targets(items, p.confirmed_game_nos(season), since=since, only=set(game_nos))
            if targets:
                batches += p.collect_games(targets)
                seasons.append(season)
                keys += [g.key for g in targets]
        if not keys:
            raise AirflowSkipException(f"watched games have not finished yet: {watch}")
        return {"seasons": seasons, "batches": batches, "games": keys}

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        return p.raw_arrived(result["batches"])

    @task
    def curate(result: dict):
        p.run_curation(result["seasons"])

    @task
    def build_marts():
        p.run_marts()

    @task
    def report(result: dict):
        since = p.now_kst().date() - timedelta(days=1)
        for season in result["seasons"]:
            for game_no, scheduled_at, status in p.match_report(season, since):
                print(f"{season}-{game_no} {scheduled_at:%m-%d %H:%M} {status}")
        print("collected:", ", ".join(result["games"]))

    result = collect_finished(find_games_to_watch())
    wait_for_raw(result) >> curate(result) >> build_marts() >> report(result)


kovo_postgame()
