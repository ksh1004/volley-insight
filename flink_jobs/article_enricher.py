"""article-enricher: dedupe and tag articles, then store them for the site and search (PIPE-07, design.md 1.9).

  Kafka news.article  ->  drop repeats  ->  tag teams/players  ->  PostgreSQL article(+_team, _player)
                                                                  Elasticsearch `articles` (alias of articles-v2)

- Repeats: the collector re-sends the latest feed items every hour, so most messages are repeats. Keyed state
  remembers each URL, then each normalized title (same story under another URL), for 7 days (state TTL).
- Tags: article_tags.py (team names and aliases, recent players; dictionary reloaded from PostgreSQL hourly).
- Writes are upserts by URL (Elasticsearch id = sha1(url)), so replaying messages after a restart is harmless.
  The Kafka position and the 7-day state are in the checkpoints; submit.sh restores the newest one.
- Each stored article prints one line (`stored ...`) to the task manager log, which Filebeat ships to Kibana.

Run (stream profile; flink-job-keeper keeps it submitted):
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
INDEX_ALIAS, INDEX = "articles", "articles-v2"  # new mapping -> new version, reindex, move the alias
DEDUPE_DAYS = 7
DICTIONARY_REFRESH_SEC = 3600

log = logging.getLogger("article-enricher")


def pg_connect():
    import psycopg2

    return psycopg2.connect(host=os.environ.get("POSTGRES_HOST", "postgres"), port=os.environ.get("POSTGRES_PORT", "5432"),
                            dbname=os.environ.get("SERVICE_DB", "service"), user=os.environ["POSTGRES_USER"],
                            password=os.environ["POSTGRES_PASSWORD"])


def parse(line: str):
    """Envelope -> flat article JSON (None for anything malformed)."""
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
    def key(value):
        a = json.loads(value)
        if kind == "url":
            return a["url"]
        from article_tags import title_key

        return title_key(a["title"]) or a["url"]
    return key


class FirstSeen(KeyedProcessFunction):
    """Passes the first element of each key and drops repeats for DEDUPE_DAYS."""

    def open(self, ctx: RuntimeContext):
        desc = ValueStateDescriptor("seen", Types.BOOLEAN())
        desc.enable_time_to_live(StateTtlConfig.new_builder(Time.days(DEDUPE_DAYS))
                                 .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
                                 .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
                                 .build())
        self.seen = ctx.get_state(desc)

    def process_element(self, value, ctx):
        if self.seen.value():
            return
        self.seen.update(True)
        yield value


class Tag(MapFunction):
    def open(self, ctx: RuntimeContext):
        self.dictionary, self.loaded = None, 0.0

    def _dictionary(self):
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


UPSERT = """
    INSERT INTO article (url, title, summary, publisher, published_at, source, collected_at)
    VALUES (%(url)s, %(title)s, %(summary)s, %(publisher)s, %(published_at)s, %(source)s, %(collected_at)s)
    ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title, summary = EXCLUDED.summary,
        publisher = EXCLUDED.publisher, published_at = EXCLUDED.published_at, updated_at = now()
    RETURNING id
"""


class Store(MapFunction):
    """Upsert into PostgreSQL (article + tag tables in one transaction) and Elasticsearch."""

    def open(self, ctx: RuntimeContext):
        from elasticsearch import Elasticsearch

        self.es = Elasticsearch(ES_URL, request_timeout=30, retry_on_timeout=True, max_retries=3)
        if not self.es.indices.exists_alias(name=INDEX_ALIAS):
            with open(os.path.join(JOBS_DIR, "es", "articles.json"), encoding="utf-8") as f:
                body = json.load(f)
            if not self.es.indices.exists(index=INDEX):
                self.es.indices.create(index=INDEX, settings=body["settings"], mappings=body["mappings"])
            self.es.indices.put_alias(index=INDEX, name=INDEX_ALIAS)
        self.conn = None

    def _pg(self):
        if self.conn is None or self.conn.closed:
            self.conn = pg_connect()
        return self.conn

    def map(self, value):
        a = json.loads(value)
        a["summary"] = a.get("summary") or ""
        conn = self._pg()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(UPSERT, a)
                article_id = cur.fetchone()[0]
                cur.execute("DELETE FROM article_team WHERE article_id = %s", (article_id,))
                cur.execute("INSERT INTO article_team (article_id, team_id) SELECT %s, id FROM team WHERE code = ANY(%s)",
                            (article_id, a["team_codes"]))
                cur.execute("DELETE FROM article_player WHERE article_id = %s", (article_id,))
                cur.execute("INSERT INTO article_player (article_id, player_id) SELECT %s, id FROM player WHERE code = ANY(%s)",
                            (article_id, a["player_codes"]))
        except Exception:
            conn.close()  # reconnect on the next element (the job restarts and replays this one)
            raise
        doc = {k: a.get(k) for k in ("url", "title", "summary", "publisher", "source", "published_at", "collected_at",
                                     "team_codes", "team_names", "player_codes", "player_names")}
        self.es.index(index=INDEX_ALIAS, id=hashlib.sha1(a["url"].encode()).hexdigest(), document=doc)
        return f"stored {a['publisher']} | {a['title'][:50]} | teams={a['team_names']} players={a['player_names']}"

    def close(self):
        if self.conn is not None:
            self.conn.close()


def main():
    config = Configuration()
    config.set_string("pipeline.name", "article-enricher")
    # set here, not only in the cluster config: a job takes these from whoever submits it (raw_archiver.py)
    config.set_string("execution.checkpointing.interval", "30 s")
    config.set_string("state.checkpoints.dir", "hdfs://namenode:9000/flink/checkpoints/article-enricher")
    config.set_string("execution.checkpointing.externalized-checkpoint-retention", "RETAIN_ON_CANCELLATION")
    env = StreamExecutionEnvironment.get_execution_environment(config)
    env.add_python_file(os.path.join(JOBS_DIR, "article_tags.py"))

    source = (KafkaSource.builder()
              .set_bootstrap_servers(KAFKA)
              .set_topics("news.article")
              .set_group_id("article-enricher")
              .set_starting_offsets(KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.EARLIEST))
              .set_value_only_deserializer(SimpleStringSchema())
              .build())

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
