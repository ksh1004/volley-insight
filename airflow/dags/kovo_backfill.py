"""kovo_backfill: 지난 시즌들을 수집하고 정제한다(COL-04, PIPE-08: 과거 데이터 채우기). 파라미터를 주고 손으로 실행.

  seasons         "001-021" 또는 "019,020"  (001 = 2005, 022 = 2025-26)
  limit           시즌마다 최대 이만큼의 경기만(빠른 시험용). 비우면 전부
  skip_confirmed  PostgreSQL에서 이미 CONFIRMED인 경기는 건너뜀(다시 돌려도 빠진 것만 받는다)

시즌마다: 일정 → 끝난 경기의 박스스코어 + 문자중계 → HDFS 대기 → Spark 정제.
모든 시즌이 끝나면: 분석 마트(전체 역사로 Elo, 시즌 요약).
KOVO 요청(1초 1회를 공유)과 Spark 때문에 시즌은 하나씩 차례로 돌지만, N시즌을 정제하는 동안 N+1시즌은
수집할 수 있다. 시즌 하나 전체는 요청 약 1,300번(약 22분)이다.

  동적 작업(expand): 시즌 목록 길이만큼 one_season 작업 그룹이 실행 시점에 만들어진다.
"""
from datetime import date

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
        """파라미터의 시즌 범위 문자열을 시즌 코드 목록으로 푼다."""
        seasons = p.parse_seasons(get_current_context()["params"]["seasons"])
        print(f"{len(seasons)} season(s): {seasons}")
        return seasons

    @task
    def season_list() -> dict:
        """시즌 목록을 한 번 수집한다(모든 시즌이 이 배치를 함께 기다린다)."""
        batch, current, _ = p.collect_season_list()
        print(f"current season: {current}")
        return batch

    @task_group
    def one_season(season: str, season_batch: dict):
        """시즌 하나: 수집 → HDFS 대기 → 정제."""
        @task(max_active_tis_per_dag=1)  # 수집은 한 번에 한 시즌만(KOVO 요청 간격을 지키기 위해)
        def collect(season: str, season_batch: dict) -> dict:
            """그 시즌 일정을 받고 끝난 경기를 골라 상세·문자중계를 수집한다."""
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
            """보낸 메시지가 모두 HDFS에 보관됐는가(최대 1시간)."""
            return p.raw_arrived(result["batches"])

        @task(max_active_tis_per_dag=1)  # 정제도 한 번에 한 시즌만(Spark 메모리)
        def curate(result: dict):
            """그 시즌을 정제한다."""
            p.run_curation([result["season"]])

        result = collect(season, season_batch)
        wait_for_raw(result) >> curate(result)

    # 모든 시즌의 정제가 끝나면(일부가 실패했어도) 실행: Elo는 전체 역사가 필요하다
    @task(trigger_rule="all_done")
    def build_marts():
        """분석 마트를 다시 만든다."""
        p.run_marts()

    # season_batch는 모든 시즌에 같은 값(partial), season은 시즌마다 다른 값(expand)
    one_season.partial(season_batch=season_list()).expand(season=plan()) >> build_marts()


kovo_backfill()
