"""live_replay: replay a finished game through the live pipeline (PIPE-11), for development and demos.

Trigger with parameters (Airflow UI "Trigger DAG w/ config"):
  match  season-game number of a finished game with play-by-play (2023-24 on), e.g. 023-6
  speed  1 = real pace, 10 = ten times faster (a 3-set game takes about 9 minutes)

  1. replay: one message per rally to Kafka kovo.live, mode=REPLAY with a new session id
  2. wait_for_live: Flink live-metrics has followed this session to the end of the game
     (live_match_state.session = this replay, status FINISHED)
Open /matches/{match}/live on the site while it runs. The site labels it as a replay; the match itself
(status, official records) is not changed.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p


@dag(
    dag_id="live_replay",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=alerts.default_args(retries=0),
    on_failure_callback=alerts.on_dag_failure,
    params={
        "match": Param("023-6", type="string", pattern=r"^\d{3}-\d+$", description="season-game number, e.g. 023-6"),
        "speed": Param(10, type="number", minimum=1, maximum=600, description="1 = real pace"),
    },
    tags=["live", "replay"],
    doc_md=__doc__,
)
def live_replay():
    @task(execution_timeout=timedelta(hours=4))
    def replay(params=None) -> dict:
        result = p.replay_match(params["match"], float(params["speed"]))
        print(f"replayed {result['rallies']} rallies of {result['match']} (session {result['session']})")
        return result

    @task.sensor(poke_interval=15, timeout=600, mode="reschedule")
    def wait_for_live(result: dict) -> bool:
        row = p.live_state(result["match"])
        print(f"live_match_state: {row}")
        return bool(row) and row[0] == result["session"] and row[1] == "FINISHED"

    wait_for_live(replay())


live_replay()
