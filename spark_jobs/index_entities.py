"""Build the Elasticsearch `entities` index for search and autocomplete (WEB-10, design.md 2.5).

  input   HDFS /curated/kovo/{team,player,player_match_stat,match}  (all seasons)
  output  Elasticsearch entities-<timestamp>, then the alias `entities` is moved to it and older copies dropped

One document per team and per player, with the latest name, team and position. `weight` ranks suggestions:
current teams first, then players by games in the last year, then career games. The index is rebuilt in
full every run (a few thousand documents), so renamed or removed entries never linger.
Written with the elasticsearch-hadoop connector (spark-submit --jars .../elasticsearch-spark.jar).

Run: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh index_entities.py
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

CURATED = os.environ.get("CURATED_PATH", "hdfs://namenode:9000/curated/kovo")
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200").rstrip("/")
ALIAS = "entities"
MAPPING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "es", "entities.json")


def es(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{ES_URL}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def latest(df, keys, order):
    w = Window.partitionBy(*keys).orderBy(F.col(order).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")


def build(spark):
    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    teams = latest(read("team"), ["code"], "last_seen_on")
    newest = teams.agg(F.max("last_seen_on")).first()[0]
    # active = played in the latest season the data has (same rule as team.is_active)
    newest_season = read("team").agg(F.max("season_code")).first()[0]
    active = read("team").where(F.col("season_code") == newest_season).select("code").distinct() \
        .withColumn("active", F.lit(True))
    team_docs = (teams.join(active, "code", "left").select(
        F.concat(F.lit("team-"), "code").alias("id"), F.lit("team").alias("type"), "code", "name",
        F.array_distinct(F.array("name", "full_name")).alias("suggest"),
        F.col("code").alias("team_code"), F.col("name").alias("team_name"), "gender",
        F.lit("").alias("position"), F.coalesce("active", F.lit(False)).alias("active"),
        F.when(F.col("active"), 100000).otherwise(10000).alias("weight"), "last_seen_on"))

    games = (read("player_match_stat").select("season_code", "game_no", "player_code", "team_code")
             .join(read("match").select("season_code", "game_no", "game_date"), ["season_code", "game_no"]))
    year_ago = F.date_sub(F.lit(newest), 365)
    per_player = games.groupBy("player_code").agg(
        F.count("*").alias("career_games"),
        F.sum(F.when(F.col("game_date") >= year_ago, 1).otherwise(0)).alias("recent_games"))
    last_team = latest(games, ["player_code"], "game_date").select("player_code", F.col("team_code"))
    players = latest(read("player").where(F.length(F.trim("name")) > 0), ["code"], "last_seen_on")
    names = teams.select(F.col("code").alias("team_code"), F.col("name").alias("team_name"))
    player_docs = (players.join(per_player, players.code == per_player.player_code)
                   .join(last_team, "player_code").join(names, "team_code", "left")
                   .select(F.concat(F.lit("player-"), "code").alias("id"), F.lit("player").alias("type"), "code",
                           "name", F.array("name").alias("suggest"), "team_code",
                           F.coalesce("team_name", F.lit("")).alias("team_name"), "gender",
                           F.coalesce("position", F.lit("")).alias("position"),
                           (F.col("recent_games") > 0).alias("active"),
                           (F.col("recent_games") * 100 + F.col("career_games")).cast("int").alias("weight"),
                           "last_seen_on"))
    return team_docs.unionByName(player_docs).withColumn("last_seen_on", F.date_format("last_seen_on", "yyyy-MM-dd"))


def main():
    spark = SparkSession.builder.appName("index-entities").getOrCreate()
    docs = build(spark).cache()
    counts = {r["type"]: r["count"] for r in docs.groupBy("type").count().collect()}
    print("entities:", counts)

    index = f"{ALIAS}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    with open(MAPPING, encoding="utf-8") as f:
        es("PUT", f"/{index}", json.load(f))
    (docs.write.format("org.elasticsearch.spark.sql")
     .option("es.nodes", ES_URL.split("://")[-1].split(":")[0])
     .option("es.port", ES_URL.rsplit(":", 1)[-1] if ES_URL.count(":") > 1 else "9200")
     .option("es.nodes.wan.only", "true")
     .option("es.index.auto.create", "false")
     .option("es.mapping.id", "id")
     .option("es.mapping.exclude", "id")
     .mode("append").save(index))
    es("POST", f"/{index}/_refresh")
    indexed = es("GET", f"/{index}/_count")["count"]
    if indexed != sum(counts.values()):
        raise RuntimeError(f"{index} has {indexed} documents, expected {sum(counts.values())}; alias not moved")

    old = [name for name in es("GET", f"/{ALIAS}-*/_alias") if name != index]
    actions = [{"add": {"index": index, "alias": ALIAS}}] + [{"remove_index": {"index": name}} for name in old]
    es("POST", "/_aliases", {"actions": actions})
    print(f"alias {ALIAS} -> {index} ({indexed} documents); removed {old or 'nothing'}")
    spark.stop()


if __name__ == "__main__":
    main()
