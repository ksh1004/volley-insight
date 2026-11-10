"""kovo_postgame: 방금 끝난 경기의 박스스코어와 문자중계를 수집한다(COL-03, COL-10, NFR-13: 경기 후 빠른 반영).

14:00부터 23:40까지 20분마다(주말 경기는 14:00/16:00, 평일 경기는 19:00 시작):
  1. 지켜볼 경기: 어제 이후 시작했고 아직 CONFIRMED가 아닌 경기(PostgreSQL에서). 없으면 건너뜀 →
     경기 없는 날에는 KOVO에 요청을 하나도 보내지 않는다.
  2. 그 시즌 일정을 새로 받아(결과가 여기서 옴) 끝난 경기를 고르고, 박스스코어와 문자중계를 수집. 끝난 게 없으면 건너뜀.
  3. raw-archiver가 모든 메시지를 HDFS /raw/에 쓸 때까지 기다림
  4. 그 시즌 Spark 정제 → 경기가 CONFIRMED가 된다(검사에 걸리면 FINISHED로 남음. 예: KOVO가 아직 문자중계를
     올리지 않음. 그러면 다음 실행이 다시 수집한다)
  5. 분석 마트(새 결과를 반영한 Elo, 시즌 요약)
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
        """{시즌: [경기 번호]}. 없으면 건너뛰기(Skip) 예외로 이후 작업도 모두 건너뛴다."""
        watch = p.games_to_watch(p.now_kst())
        if not watch:
            raise AirflowSkipException("no started, unconfirmed game since yesterday")
        return watch

    @task
    def collect_finished(watch: dict) -> dict:
        """지켜볼 경기 중 끝난 경기만 수집한다. 확정된 경기는 다시 받지 않는다."""
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
        """보낸 메시지가 모두 HDFS에 보관됐는가."""
        return p.raw_arrived(result["batches"])

    @task
    def curate(result: dict):
        """수집한 경기가 있는 시즌을 정제한다."""
        p.run_curation(result["seasons"])

    @task
    def build_marts():
        """분석 마트를 다시 만든다."""
        p.run_marts()

    @task
    def report(result: dict):
        """어제 이후 경기들의 상태를 로그로 남긴다."""
        since = p.now_kst().date() - timedelta(days=1)
        for season in result["seasons"]:
            for game_no, scheduled_at, status in p.match_report(season, since):
                print(f"{season}-{game_no} {scheduled_at:%m-%d %H:%M} {status}")
        print("collected:", ", ".join(result["games"]))

    result = collect_finished(find_games_to_watch())
    wait_for_raw(result) >> curate(result) >> build_marts() >> report(result)


kovo_postgame()
