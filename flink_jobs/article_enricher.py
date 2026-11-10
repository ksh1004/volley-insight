"""article-enricher: 기사 중복을 없애고 태그를 단 뒤, 사이트와 검색용으로 저장한다(PIPE-07, 설계서 1.9).

  Kafka news.article → 중복 제거 → 팀/선수 태그 → PostgreSQL article(+ article_team, article_player)
                                                  Elasticsearch `articles` (articles-v2의 별칭)

- 중복: 수집기는 매시간 피드의 최신 기사를 다시 보내므로 메시지 대부분이 중복이다. 키별 상태(keyed state)에
  각 URL을, 이어서 정규화한 제목(다른 URL의 같은 기사)을 7일 동안 기억한다(상태 TTL).
- 태그: article_tags.py(팀 이름·별칭, 최근 선수. 사전은 1시간마다 PostgreSQL에서 다시 읽음).
- 저장은 URL 기준 upsert(있으면 갱신, 없으면 추가)이고 Elasticsearch 문서 id는 sha1(url)이다.
  그래서 재시작 후 메시지를 다시 처리해도 문제가 없다. Kafka 위치와 7일치 상태는 체크포인트에 들어 있고,
  submit.sh가 가장 최근 체크포인트에서 복원한다.
- 저장한 기사마다 TaskManager 로그에 한 줄(`stored ...`)을 찍고, Filebeat가 이를 Kibana로 보낸다.

실행(stream 프로필. flink-job-keeper가 계속 제출 상태를 유지한다):
  docker compose exec flink-jobmanager sh /opt/flink/usrlib/jobs/submit.sh article_enricher.py article-enricher
"""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone

from pyflink.common import Configuration, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.time import Time
from pyflink.datastream import KeyedProcessFunction, MapFunction, RuntimeContext, StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import KafkaOffsetResetStrategy, KafkaOffsetsInitializer, KafkaSource
from pyflink.datastream.state import StateTtlConfig, ValueStateDescriptor

JOBS_DIR = os.environ.get("JOBS_DIR", "/opt/flink/usrlib/jobs")
KAFKA = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
# 매핑을 바꾸면 새 버전 색인(articles-v3 …)을 만들어 다시 색인한 뒤 별칭만 옮긴다(검색은 항상 별칭으로)
INDEX_ALIAS, INDEX = "articles", "articles-v2"
DEDUPE_DAYS = 7  # 중복 판정 기억 기간
DICTIONARY_REFRESH_SEC = 3600  # 태그 사전 갱신 주기(초)

log = logging.getLogger("article-enricher")


def pg_connect():
    """서비스 DB(PostgreSQL) 접속. 접속 정보는 환경 변수에서 읽는다."""
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def parse(line: str):
    """수집기 봉투(JSON 문자열)를 납작한 기사 JSON 문자열로 바꾼다. 형식이 잘못됐으면 None(다음 단계에서 버림).
    collected_at = 수집 시각, published_at이 없으면 수집 시각으로 채운다."""
    try:
        env = json.loads(line)
        a = env["payload"]
        if not (a.get("url") and a.get("title")):
            return None
        a["collected_at"] = env.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
        a["published_at"] = a.get("published_at") or a["collected_at"]
        return json.dumps(a, ensure_ascii=False)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def dedupe_key(kind):
    """중복 판정 키를 뽑는 함수를 만든다. kind="url"이면 URL, "title"이면 정규화 제목(키가 없으면 URL)."""
    def key(value):
        a = json.loads(value)
        if kind == "url":
            return a["url"]
        from article_tags import title_key  # TaskManager에서 실행될 때 import(add_python_file로 배포됨)

        return title_key(a["title"]) or a["url"]
    return key


class FirstSeen(KeyedProcessFunction):
    """키마다 처음 온 것만 통과시키고, DEDUPE_DAYS 동안 같은 키는 버린다.
    상태 TTL: 기록한 지 7일이 지나면 Flink가 그 키의 상태를 지운다(상태가 끝없이 커지지 않게)."""

    def open(self, ctx: RuntimeContext):
        desc = ValueStateDescriptor("seen", Types.BOOLEAN())
        desc.enable_time_to_live(StateTtlConfig.new_builder(Time.days(DEDUPE_DAYS))
                                 .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
                                 .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                                 .build())
        self.seen = ctx.get_state(desc)

    def process_element(self, value, ctx):
        if self.seen.value():
            return  # 이미 본 키: 버림
        self.seen.update(True)
        yield value


class Tag(MapFunction):
    """기사에 팀 코드·선수 코드·선수 이름·팀 이름 태그를 붙인다."""

    def open(self, ctx: RuntimeContext):
        self.dictionary, self.loaded = None, 0.0

    def _dictionary(self):
        """태그 사전. 처음이거나 1시간이 지났으면 DB에서 다시 읽는다(새 선수·팀 이름 변경 반영)."""
        if self.dictionary is None or time.time() - self.loaded > DICTIONARY_REFRESH_SEC:
            from article_tags import load_dictionary

            conn = pg_connect()
            try:
                self.dictionary = load_dictionary(conn)
            finally:
                conn.close()
            self.loaded = time.time()
        return self.dictionary

    def map(self, value):
        a = json.loads(value)
        d = self._dictionary()
        a["team_codes"], a["player_codes"], a["player_names"] = d.tag(a["title"], a.get("summary", ""))
        a["team_names"] = [d.team_names[c] for c in a["team_codes"]]
        return json.dumps(a, ensure_ascii=False)


# URL이 이미 있으면 내용을 갱신하고, 없으면 새로 넣는다. 어느 쪽이든 기사 id를 돌려받는다
UPSERT = """
    INSERT INTO article (url, title, summary, publisher, published_at, source, collected_at)
    VALUES (%(url)s, %(title)s, %(summary)s, %(publisher)s, %(published_at)s, %(source)s, %(collected_at)s)
    ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title, summary = EXCLUDED.summary,
        publisher = EXCLUDED.publisher, published_at = EXCLUDED.published_at, updated_at = now()
    RETURNING id
"""


class Store(MapFunction):
    """PostgreSQL(기사 + 태그 테이블을 한 트랜잭션으로)과 Elasticsearch에 upsert 한다."""

    def open(self, ctx: RuntimeContext):
        """Elasticsearch 접속을 열고, 기사 색인(별칭)이 없으면 es/articles.json 매핑으로 만든다."""
        from elasticsearch import Elasticsearch

        self.es = Elasticsearch(ES_URL, request_timeout=30, retry_on_timeout=True, max_retries=3)
        if not self.es.indices.exists_alias(name=INDEX_ALIAS):
            with open(os.path.join(JOBS_DIR, "es", "articles.json"), encoding="utf-8") as f:
                body = json.load(f)
            if not self.es.indices.exists(index=INDEX):
                self.es.indices.create(index=INDEX, settings=body["settings"], mappings=body["mappings"])
            self.es.indices.put_alias(index=INDEX, name=INDEX_ALIAS)
        self.conn = None  # DB 연결은 처음 쓸 때 연다

    def _pg(self):
        """열린 DB 연결(끊겼으면 다시 연다)."""
        if self.conn is None or self.conn.closed:
            self.conn = pg_connect()
        return self.conn

    def map(self, value):
        """기사 하나를 저장하고 로그용 한 줄을 돌려준다. 태그 테이블은 지우고 다시 넣는다(태그가 바뀌었을 수 있으므로)."""
        a = json.loads(value)
        a["summary"] = a.get("summary") or ""
        conn = self._pg()
        try:
            with conn, conn.cursor() as cur:  # with conn: 블록이 끝나면 커밋, 예외면 롤백
                cur.execute(UPSERT, a)
                article_id = cur.fetchone()[0]
                cur.execute("DELETE FROM article_team WHERE article_id = %s", (article_id,))
                cur.execute("INSERT INTO article_team (article_id, team_id) SELECT %s, id FROM team WHERE code = ANY(%s)",
                            (article_id, a["team_codes"]))
                cur.execute("DELETE FROM article_player WHERE article_id = %s", (article_id,))
                cur.execute("INSERT INTO article_player (article_id, player_id) SELECT %s, id FROM player WHERE code = ANY(%s)",
                            (article_id, a["player_codes"]))
        except Exception:
            conn.close()  # 다음 요소에서 다시 연결한다(작업이 재시작되며 이 요소를 다시 처리함)
            raise
        doc = {k: a.get(k) for k in ("url", "title", "summary", "publisher", "source", "published_at", "collected_at",
                                     "team_codes", "team_names", "player_codes", "player_names")}
        self.es.index(index=INDEX_ALIAS, id=hashlib.sha1(a["url"].encode()).hexdigest(), document=doc)
        return f"stored {a['publisher']} | {a['title'][:50]} | teams={a['team_names']} players={a['player_names']}"

    def close(self):
        if self.conn is not None:
            self.conn.close()


def main():
    """작업 구성: Kafka → 파싱 → 유효성 → URL 중복 제거 → 제목 중복 제거 → 태그 → 저장 → 로그 출력."""
    config = Configuration()
    config.set_string("pipeline.name", "article-enricher")
    # 클러스터 설정에만 두지 않고 여기서 정한다: 작업은 이 값을 제출한 쪽에서 가져간다(raw_archiver.py 참고)
    config.set_string("execution.checkpointing.interval", "30 s")
    config.set_string("state.checkpoints.dir", "hdfs://namenode:9000/flink/checkpoints/article-enricher")
    config.set_string("execution.checkpointing.externalized-checkpoint-retention", "RETAIN_ON_CANCELLATION")
    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.add_python_file(os.path.join(JOBS_DIR, "article_tags.py"))  # TaskManager에도 이 파일을 보낸다

    # Kafka 입력. 커밋된 오프셋부터, 없으면 가장 처음부터 읽는다
    source = (KafkaSource.builder()
              .set_bootstrap_servers(KAFKA)
              .set_topics("news.article")
              .set_group_id("article-enricher")
              .set_starting_offsets(KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.EARLIEST))
              .set_value_only_deserializer(SimpleStringSchema())
              .build())

    # key_by 뒤의 FirstSeen은 키마다 따로 상태를 가진다(같은 키의 메시지는 항상 같은 작업자에게 감)
    (env.from_source(source, WatermarkStrategy.no_watermarks(), "news.article")
        .map(parse, output_type=Types.STRING()).name("parse")
        .filter(lambda v: v is not None).name("valid")
        .key_by(dedupe_key("url"), key_type=Types.STRING())
        .process(FirstSeen(), output_type=Types.STRING()).name("new-url")
        .key_by(dedupe_key("title"), key_type=Types.STRING())
        .process(FirstSeen(), output_type=Types.STRING()).name("new-title")
        .map(Tag(), output_type=Types.STRING()).name("tag")
        .map(Store(), output_type=Types.STRING()).name("store")
        .print())
    env.execute("article-enricher")


if __name__ == "__main__":
    main()
