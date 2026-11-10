"""kovo_reprocess: 고른 경기·날짜·시즌을 다시 수집하거나 다시 정제한다(PIPE-08, ADM-02). 손으로 실행.

Django 관리자 '수동 실행' 화면(또는 Airflow의 "Trigger DAG w/ config")에서 시작한다. 대상을 하나 이상 준다:
  games      "023-9, 023-10"                  시즌-경기번호
  dates      "2026-11-05" 또는 "2026-11-01~2026-11-05"   그날(서울 시각) 예정된 경기들, 최대 31일
  seasons    "023" 또는 "020-022"             시즌 전체를 원본 데이터로만 다시 정제
  recollect  true: (games/dates의) 경기를 KOVO에서 다시 받는다. CONFIRMED 경기도 포함(예: KOVO가 기록을 고친 뒤).
             false: KOVO 요청 없이 그 시즌들을 HDFS /raw/로 다시 정제만 한다.

  1. plan: 대상을 정한다(날짜 → PostgreSQL로 그날 경기 찾기)
  2. collect(recollect일 때만): 그 시즌 일정 + 끝난 대상 경기의 박스스코어와 문자중계
  3. raw-archiver가 HDFS에 쓸 때까지 기다림
  4. 영향받은 시즌마다 그 시즌의 모든 원본으로 Spark 정제(경기마다 가장 최근 수집본이 이김, COL-09),
     관리자 보정과 품질 검사 포함
  5. 분석 마트, 그리고 대상 경기마다의 상태를 로그에 남김
정제는 시즌 단위라 시즌 전체를 다시 정제한다. 시즌 전체를 다시 '수집'하려면 kovo_backfill을 쓴다.
"""
from datetime import date

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.sdk import Param, dag, get_current_context, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

MAX_RECOLLECT = 50  # 경기당 KOVO 요청 약 6번(1초 1회). 이보다 많으면 백필로 할 일이다


@dag(
    dag_id="kovo_reprocess",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=alerts.default_args(),
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "manual"],
    doc_md=__doc__,
    params={
        "games": Param("", type="string", description="e.g. 023-9, 023-10"),
        "dates": Param("", type="string", description="e.g. 2026-11-05 or 2026-11-01~2026-11-05"),
        "seasons": Param("", type="string", description="e.g. 023 or 020-022 (re-curate only)"),
        "recollect": Param(True, type="boolean", description="fetch games/dates from KOVO again"),
    },
)
def kovo_reprocess():
    @task(retries=0)
    def plan() -> dict:
        """파라미터를 검사하고 대상을 정한다: {games: {시즌: [경기번호]}, seasons: 정제할 시즌들, recollect}.
        형식이 틀리거나 대상이 없으면 바로 실패(재시도 없음)하고, 그 메시지가 관리자 화면에 보인다."""
        params = get_current_context()["params"]
        try:
            games = p.parse_games(params.get("games") or "")
            if (params.get("dates") or "").strip():
                games = p.merge_games(games, p.games_on_dates(*p.parse_dates(params["dates"])))
            seasons = p.parse_seasons(params["seasons"]) if (params.get("seasons") or "").strip() else []
        except ValueError as e:
            raise AirflowFailException(str(e)) from None
        if not games and not seasons:
            raise AirflowFailException("대상이 없습니다: games, dates, seasons 중 하나는 있어야 합니다 "
                                       "(날짜를 줬다면 그날 경기가 DB에 없는 것입니다).")
        n = sum(len(g) for g in games.values())
        recollect = bool(params.get("recollect")) and n > 0
        if recollect and n > MAX_RECOLLECT:
            raise AirflowFailException(f"다시 받을 경기가 {n}개입니다. {MAX_RECOLLECT}개를 넘으면 kovo_backfill을 쓰세요.")
        out = {"games": games, "seasons": sorted(set(seasons) | set(games)), "recollect": recollect}
        print(f"games: {games or '-'}; seasons to curate: {out['seasons']}; recollect: {recollect}")
        return out

    @task(max_active_tis_per_dag=1)
    def collect(target: dict) -> dict:
        """recollect면 대상 경기를 다시 수집한다. KOVO 일정상 아직 안 끝난 경기는 건너뛰고 로그에 남긴다."""
        batches, keys, skipped = [], [], []
        if target["recollect"]:
            for season, gnums in target["games"].items():
                batch, items = p.collect_schedule(season)
                batches.append(batch)
                games = p.pick_targets(items, confirmed=set(), since=date.min, only=set(gnums))
                found = {g.gnum for g in games}
                skipped += [f"{season}-{n}" for n in gnums if n not in found]
                batches += p.collect_games(games)
                keys += [g.key for g in games]
        if skipped:
            print("not finished in KOVO's schedule, not collected:", ", ".join(skipped))
        print("collected:", ", ".join(keys) or "- (re-curation only)")
        return {**target, "batches": batches, "collected": keys}

    @task.sensor(poke_interval=60, timeout=3600, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        """보낸 메시지가 모두 HDFS에 보관됐는가(보낸 게 없으면 바로 통과)."""
        return p.raw_arrived(result["batches"])

    @task(max_active_tis_per_dag=1)
    def curate(result: dict):
        """영향받은 시즌들을 정제한다."""
        p.run_curation(result["seasons"])

    @task
    def build_marts():
        """분석 마트를 다시 만든다."""
        p.run_marts()

    @task
    def report(result: dict):
        """대상 경기들의 최종 상태를 로그로 남긴다(관리자가 결과를 확인하는 곳)."""
        for season, gnum, scheduled_at, status in p.game_report(result["games"]):
            print(f"{season}-{gnum} {scheduled_at:%Y-%m-%d %H:%M} {status}")
        print("curated seasons:", ", ".join(result["seasons"]))

    result = collect(plan())
    wait_for_raw(result) >> curate(result) >> build_marts() >> report(result)


kovo_reprocess()
