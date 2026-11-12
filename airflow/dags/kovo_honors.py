"""kovo_honors: 선수별 수상·주요 기록을 수집하고 정제한다(COL-04). 매주 월요일 05:00.

  scope  current = 최근 400일 안에 경기 기록이 있는 선수(약 300명, 약 5분) — 매주 기본값
         all     = DB의 모든 선수(약 1,100명, 약 20분) — 처음 한 번, 또는 과거 선수 기록을 다시 받을 때 손으로 실행

흐름: 대상 선수 고르기 + 수집(선수마다 KOVO /players/{코드}/honor 1번) → HDFS 대기 → Spark 정제(curate_honors.py)
정제는 HDFS에 남은 모든 선수의 마지막 응답으로 player_honor 표 전체를 다시 만든다. 그래서 매주 현역만 다시 받아도
은퇴 선수의 기록은 처음 받은 그대로 남는다. 수상은 라운드가 끝날 때(약 2~3주마다) 늘어나므로 주 1회면 충분하다.
KOVO 요청은 다른 DAG와 같은 1초 1회 제한(잠금 파일)을 공유한다.
"""
import pendulum
from airflow.sdk import Param, dag, get_current_context, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args()


@dag(
    dag_id="kovo_honors",
    schedule="0 5 * * 1",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "collect"],
    doc_md=__doc__,
    params={"scope": Param("current", enum=["current", "all"], description="current players, or every player")},
)
def kovo_honors():
    @task
    def collect() -> dict:
        """대상 선수를 고르고 수상·주요 기록을 발행한다. 다음 작업에 넘길 배치 정보를 돌려준다."""
        everyone = get_current_context()["params"]["scope"] == "all"
        codes = p.honor_targets(everyone)
        print(f"{len(codes)} player(s), about {len(codes) // 60 + 1} min at 1 KOVO request/s")
        batch, skipped = p.collect_honors(codes)
        if skipped:
            print(f"not on KOVO ({len(skipped)}): {', '.join(skipped)}")
        return batch

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(batch: dict) -> bool:
        """보낸 메시지가 모두 HDFS에 보관됐는가(1분마다 확인, 최대 30분)."""
        return p.raw_arrived([batch])

    @task
    def curate():
        """HDFS의 수상 원본으로 player_honor를 다시 만든다(Spark curate_honors.py)."""
        p.run_curate_honors()

    batch = collect()
    wait_for_raw(batch) >> curate()


kovo_honors()
