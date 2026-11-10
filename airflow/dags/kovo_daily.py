"""kovo_daily: 매일 아침 현재 시즌을 새로 고친다(COL-01, COL-02, PIPE-04). 데이터 파이프라인의 중심 DAG.

매일 06:00:
  1. 시즌 목록 → 현재 시즌(경기 리그가 있는 가장 최근 시즌 코드)
  2. 그 시즌의 일정과 결과
  3. 따라잡기: 최근 7일 동안 끝났지만 CONFIRMED가 아닌 경기(예: kovo_postgame이 못 돈 경우)의 상세·문자중계
  4. raw-archiver가 모든 메시지를 HDFS /raw/에 쓸 때까지 기다림
  5. 그 시즌의 Spark 정제
  6. 분석 마트: Elo, 팀/선수 시즌 요약(모든 시즌)
  7. Elasticsearch `entities`(팀/선수 검색과 자동완성)를 정제 데이터로 다시 만듦
  8. 선수 유형(K-means)과 비슷한 선수, 모든 시즌(ANL-06): player_type, player_style
     → 포지션별 능력치(player_ratings): player_rating, player_rating_career
  9. 품질 관문(PIPE-10): 최근 7일 경기에 OPEN 상태의 ERROR 문제가 있거나(예: 다시 수집해도 문자중계 누락),
     정규리그 경기가 시작 6시간 뒤에도 결과가 없으면 실패한다. KOVO 자체 데이터의 알려진 빈틈은 Django 관리자에서
     '확인함'으로 바꾸면 더는 실패시키지 않는다.
지난 하루 사이에 경기 단위 보정(ADM-03)이 바뀐 시즌은 현재 시즌과 함께 다시 정제한다.

작업 흐름(아래 맨 끝 참고): collect → wait_for_raw → curate → build_marts → report
                           curate → quality_gate,  build_marts → index_entities,  build_marts → player_types → player_ratings
"""
from datetime import timedelta

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p

DEFAULT_ARGS = alerts.default_args()  # 재시도 3번(5분 간격) + 실패 알림
CATCH_UP_DAYS = 7  # 며칠 전 경기까지 따라잡을지


@dag(
    dag_id="kovo_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    on_failure_callback=alerts.on_dag_failure,
    tags=["kovo", "collect"],
    doc_md=__doc__,
)
def kovo_daily():
    @task
    def collect() -> dict:
        """시즌 목록·일정·따라잡을 경기를 수집하고, 다음 작업에 넘길 정보(시즌, 배치들, 경기 키들)를 돌려준다.
        배치(batch) = Kafka로 보낸 메시지 묶음의 식별 정보. HDFS에 다 쌓였는지 확인할 때 쓴다."""
        season_batch, season, leagues = p.collect_season_list()
        schedule_batch, items = p.collect_schedule(season, leagues)
        since = p.now_kst().date() - timedelta(days=CATCH_UP_DAYS)
        targets = p.pick_targets(items, p.confirmed_game_nos(season), since=since)
        print(f"season {season}: {len(items)} games in schedule, {len(targets)} finished game(s) to collect")
        return {"season": season, "batches": [season_batch, schedule_batch] + p.collect_games(targets),
                "games": [g.key for g in targets]}

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        """보낸 메시지가 모두 HDFS에 보관됐는가(1분마다 확인, 최대 30분)."""
        return p.raw_arrived(result["batches"])

    @task
    def curate(result: dict):
        """현재 시즌 + 어제 이후 보정이 바뀐 시즌을 정제한다(Spark curate_kovo.py)."""
        corrected = p.corrected_seasons(p.now_kst() - timedelta(days=1))
        if corrected:
            print("seasons with new corrections:", ", ".join(corrected))
        p.run_curation([result["season"], *corrected])

    @task
    def build_marts():
        """분석 마트(Elo, 시즌 요약)를 다시 만든다."""
        p.run_marts()

    @task
    def index_entities():
        """검색용 팀/선수 색인을 다시 만든다."""
        p.run_index_entities()

    @task
    def player_types():
        """선수 유형 군집화와 비슷한 선수를 다시 계산한다."""
        p.run_player_types()

    @task
    def player_ratings():
        """포지션별 능력치(시즌·통산 카드)를 다시 계산한다."""
        p.run_player_ratings()

    @task(retries=0)  # 데이터 문제는 다시 해도 같으므로 재시도하지 않는다
    def quality_gate():
        """최근 7일 데이터 품질을 확인한다. 문제가 있으면 실패시켜 알림이 가게 한다(다른 작업은 계속 진행)."""
        now = p.now_kst()
        report = p.quality_report(now - timedelta(days=CATCH_UP_DAYS), now)
        print("open issues of all seasons:", report["open"] or "none")
        problems = p.gate_problems(report)
        if problems:
            raise AirflowFailException("data quality: " + "; ".join(problems)
                                       + " (fix, or acknowledge a source gap in Django admin > 품질 검사 결과)")
        print(f"quality gate passed: no open error in the last {CATCH_UP_DAYS} days")

    @task
    def report(result: dict):
        """최근 7일 경기의 상태를 로그로 남긴다(확정 안 된 경기 목록)."""
        since = p.now_kst().date() - timedelta(days=CATCH_UP_DAYS)
        rows = p.match_report(result["season"], since)
        not_confirmed = [f"{result['season']}-{g} ({s})" for g, _, s in rows if s != "CONFIRMED"]
        print(f"last {CATCH_UP_DAYS} days: {len(rows)} game(s), not confirmed: {not_confirmed or 'none'}")
        print("collected:", ", ".join(result["games"]) or "none")

    result = collect()
    marts = build_marts()
    curated = curate(result)
    wait_for_raw(result) >> curated >> marts >> report(result)
    curated >> quality_gate()
    marts >> index_entities()
    marts >> player_types() >> player_ratings()  # 하나씩 차례로: Spark 드라이버마다 이 컨테이너의 메모리가 필요하다


kovo_daily()
