"""news_collect: volleyball article metadata every hour (COL-07, PIPE-07).

  1. RSS feeds of sports sections (+ Naver news search API when NAVER_CLIENT_ID/SECRET are set)
     -> volleyball items only, title/summary/link/publisher -> Kafka `news.article`
  2. wait until raw-archiver has the messages in HDFS /raw/topic=news.article/
  3. wait until Flink article-enricher has stored every collected URL (PostgreSQL `article` and
     Elasticsearch `articles`); repeats of earlier runs are already stored, so the check covers them too
A feed that fails is skipped and reported; the run fails only when every source failed.
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
        batch, urls, failures, titles = p.collect_news()
        print(f"{len(urls)} volleyball article(s) published; failed feeds: {failures or 'none'}")
        return {"batch": batch, "urls": urls, "failures": failures, "titles": titles}

    @task.sensor(poke_interval=60, timeout=1800, mode="reschedule")
    def wait_for_raw(result: dict) -> bool:
        return p.raw_arrived([result["batch"]])

    @task.sensor(poke_interval=30, timeout=900, mode="reschedule")
    def wait_for_index(result: dict) -> bool:
        in_db, in_es = p.articles_stored(result["urls"], result.get("titles"))
        print(f"stored: PostgreSQL {in_db}/{len(result['urls'])}, Elasticsearch {in_es}/{len(result['urls'])}")
        return in_db == in_es == len(result["urls"])

    result = collect()
    wait_for_raw(result)
    wait_for_index(result)


news_collect()
