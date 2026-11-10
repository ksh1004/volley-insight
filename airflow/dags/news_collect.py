"""news_collect: 매시간 배구 기사 메타데이터를 모은다(COL-07, PIPE-07). 매시 5분에 실행.

  1. 스포츠 섹션 RSS 피드(+ NAVER_CLIENT_ID/SECRET이 설정돼 있으면 네이버 뉴스 검색 API)
     → 배구 기사만, 제목/요약/링크/언론사 → Kafka `news.article`
  2. raw-archiver가 그 메시지를 HDFS /raw/topic=news.article/ 에 저장할 때까지 기다린다
  3. Flink article-enricher가 수집한 URL을 모두 저장할 때까지 기다린다(PostgreSQL `article`과
     Elasticsearch `articles`). 이전 실행에서 이미 저장된 중복 기사도 저장돼 있으므로 함께 확인된다
실패한 피드는 건너뛰고 보고만 한다. 모든 출처가 실패했을 때만 실행이 실패한다.

  sensor(센서) = 조건이 맞을 때까지 주기적으로 확인하며 기다리는 작업. reschedule 모드라 기다리는 동안
  작업자 슬롯을 차지하지 않는다.
"""

import pendulum
from airflow.sdk import dag, task

from kovo_tasks import alerts
from kovo_tasks import pipeline as p


@dag(
    dag_id="news_collect",
    schedule="5 * * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args=alerts.default_args(retries=2),
    on_failure_callback=alerts.on_dag_failure,
    tags=["news", "collect"],
    doc_md=__doc__,
)
def news_collect():
    @task
    def collect() -> dict:
        """기사를 모아 Kafka로 보내고, 다음 작업들이 확인할 정보(배치, URL, 실패한 피드, 제목)를 넘긴다."""
        batch, urls, failures, titles = p.collect_news()
        print(f"{len(urls)} volleyball article(s) published; failed feeds: {failures or 'none'}")
        return {"batch": batch, "urls": urls, "failures": failures, "titles": titles}

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        """HDFS 원본 보관이 끝났는가(1분마다, 최대 30분)."""
        return p.raw_arrived([result["batch"]])

    @task.sensor(poke_interval=30, timeout=900, mode="reschedule")
    def wait_for_index(result: dict) -> bool:
        """기사가 DB와 검색 색인 양쪽에 모두 저장됐는가(30초마다, 최대 15분)."""
        in_db, in_es = p.articles_stored(result["urls"], result.get("titles"))
        print(f"stored: PostgreSQL {in_db}/{len(result['urls'])}, Elasticsearch {in_es}/{len(result['urls'])}")
        return in_db == in_es == len(result["urls"])

    # 작업 연결: collect의 결과를 두 센서가 각각 받아 확인한다
    result = collect()
    wait_for_raw(result)
    wait_for_index(result)


news_collect()
