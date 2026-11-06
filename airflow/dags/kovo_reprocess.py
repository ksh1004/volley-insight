"""kovo_reprocess: re-collect and/or re-curate chosen games, days or seasons (PIPE-08, ADM-02). Manual run.

Started from Django admin "수동 실행" (or Airflow "Trigger DAG w/ config"). Give at least one target:
  games      "023-9, 023-10"                  season-game numbers
  dates      "2026-11-05" or "2026-11-01~2026-11-05"   games scheduled on those days (KST), 31 days at most
  seasons    "023" or "020-022"               whole seasons, re-curated from the raw data only
  recollect  true: fetch the games (from games/dates) from KOVO again, even CONFIRMED ones, e.g. after KOVO
             corrected a record. false: no KOVO request, only re-curate their seasons from HDFS /raw/.

  1. plan: resolve the targets (dates -> games through PostgreSQL)
  2. collect (recollect only): that season's schedule + box scores and play-by-play of the finished targets
  3. wait until raw-archiver has written them to HDFS
  4. Spark curation of every affected season from all of its raw data (latest fetch of each game wins, COL-09),
     with admin corrections and quality checks
  5. analysis marts, then the status of each target game in the log
Seasons are curated whole because curation is per season; re-collecting a whole season is kovo_backfill.
"""
from datetime import date

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.sdk import Param, dag, get_current_context, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

MAX_RECOLLECT = 50  # about 6 KOVO requests per game at 1 req/s; more than this is a backfill


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
        return p.raw_arrived(result["batches"])

    @task(max_active_tis_per_dag=1)
    def curate(result: dict):
        p.run_curation(result["seasons"])

    @task
    def build_marts():
        p.run_marts()

    @task
    def report(result: dict):
        for season, gnum, scheduled_at, status in p.game_report(result["games"]):
            print(f"{season}-{gnum} {scheduled_at:%Y-%m-%d %H:%M} {status}")
        print("curated seasons:", ", ".join(result["seasons"]))

    result = collect(plan())
    wait_for_raw(result) >> curate(result) >> build_marts() >> report(result)


kovo_reprocess()
