"""live_replay: 끝난 경기를 실시간 경로로 다시 재생한다(PIPE-11). 개발과 시연용.

파라미터와 함께 실행한다(Airflow 화면의 "Trigger DAG w/ config"):
  match  문자중계가 있는 끝난 경기(2023-24부터)의 시즌-경기번호, 예: 023-6
  speed  1 = 실제 속도, 10 = 10배 빠르게(3세트 경기가 약 9분)

  1. replay: 랠리마다 메시지 하나를 Kafka kovo.live로 보낸다(mode=REPLAY, 새 세션 ID)
  2. wait_for_live: Flink live-metrics가 이 세션을 경기 끝까지 따라갔는지 확인한다
     (live_match_state.session = 이번 재생, status FINISHED)
실행하는 동안 사이트에서 /matches/{match}/live 를 열어 보면 된다. 사이트는 '다시 보기'로 표시하고,
경기 자체(상태, 공식 기록)는 바뀌지 않는다.
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
        """재생을 실행한다(긴 경기·느린 속도를 위해 최대 4시간). params는 Airflow가 실행 설정을 넣어 준다."""
        result = p.replay_match(params["match"], float(params["speed"]))
        print(f"replayed {result['rallies']} rallies of {result['match']} (session {result['session']})")
        return result

    @task.sensor(poke_interval=15, timeout=600, mode="reschedule")
    def wait_for_live(result: dict) -> bool:
        """Flink가 이번 세션을 끝까지 처리했는가(15초마다, 최대 10분)."""
        row = p.live_state(result["match"])
        print(f"live_match_state: {row}")
        return bool(row) and row[0] == result["session"] and row[1] == "FINISHED"

    wait_for_live(replay())


live_replay()
