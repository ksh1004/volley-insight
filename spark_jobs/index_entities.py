"""검색과 자동완성에 쓰는 Elasticsearch `entities` 색인을 만든다(WEB-10, 설계서 2.5).

  입력  HDFS /curated/kovo/{team,player,player_match_stat,match}  (모든 시즌)
  출력  Elasticsearch entities-<시각> 새 색인 → 별칭 `entities`를 새 색인으로 옮기고 이전 색인은 지운다

팀마다, 선수마다 문서 하나(최신 이름, 소속 팀, 포지션). `weight`는 자동완성 순서를 정한다:
지금 활동하는 팀이 먼저, 다음은 최근 1년 경기 수가 많은 선수, 그다음 통산 경기 수.
매번 색인 전체를 새로 만든다(문서 몇천 개). 그래서 이름이 바뀌거나 사라진 항목이 남지 않는다.
별칭을 옮기는 방식이라 새 색인을 만드는 동안에도 검색은 이전 색인으로 계속된다(무중단 교체).
elasticsearch-hadoop 커넥터로 쓴다(spark-submit --jars .../elasticsearch-spark.jar).

실행: docker compose exec airflow sh /opt/airflow/spark_jobs/submit.sh index_entities.py (평소에는 kovo_daily DAG)
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
# 색인 설정·매핑 파일(한글 분석기, 자동완성 필드 등)
MAPPING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "es", "entities.json")


def es(method, path, body=None):
    """Elasticsearch REST API를 부르고 JSON 응답을 돌려준다(색인 생성·별칭 이동 같은 관리 작업용)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{ES_URL}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def latest(df, keys, order):
    """keys가 같은 행 중 order가 가장 큰 행 하나만 남긴다."""
    w = Window.partitionBy(*keys).orderBy(F.col(order).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn")


def build(spark):
    """팀 문서와 선수 문서를 만들어 합친다(색인할 DataFrame)."""
    read = lambda name: spark.read.parquet(f"{CURATED}/{name}")  # noqa: E731
    teams = latest(read("team"), ["code"], "last_seen_on")
    newest = teams.agg(F.max("last_seen_on")).first()[0]
    # active = 데이터의 가장 최근 시즌에 경기한 팀(team.is_active와 같은 규칙)
    newest_season = read("team").agg(F.max("season_code")).first()[0]
    active = read("team").where(F.col("season_code") == newest_season).select("code").distinct() \
        .withColumn("active", F.lit(True))
    team_docs = (teams.join(active, "code", "left").select(
        F.concat(F.lit("team-"), "code").alias("id"), F.lit("team").alias("type"), "code", "name",
        F.array_distinct(F.array("name", "full_name")).alias("suggest"),
        F.col("code").alias("team_code"), F.col("name").alias("team_name"), "gender",
        F.lit("").alias("position"), F.coalesce("active", F.lit(False)).alias("active"),
        F.when(F.col("active"), 100000).otherwise(10000).alias("weight"), "last_seen_on"))

    # 선수별 통산 경기 수와 최근 1년 경기 수, 가장 최근 경기의 소속 팀
    games = (read("player_match_stat").select("season_code", "game_no", "player_code", "team_code")
             .join(read("match").select("season_code", "game_no", "game_date"), ["season_code", "game_no"]))
    year_ago = F.date_sub(F.lit(newest), 365)
    per_player = games.groupBy("player_code").agg(
        F.count("*").alias("career_games"),
        F.sum(F.when(F.col("game_date") >= year_ago, 1).otherwise(0)).alias("recent_games"))
    last_team = latest(games, ["player_code"], "game_date").select("player_code", F.col("team_code"))
    players = latest(read("player").where(F.length(F.trim("name")) > 0), ["code"], "last_seen_on")  # 이름 없는 선수 제외
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
    """새 색인 생성 → 문서 쓰기 → 개수 확인 → 별칭 이동·이전 색인 삭제. 개수가 다르면 별칭을 옮기지 않는다."""
    spark = SparkSession.builder.appName("index-entities").getOrCreate()
    docs = build(spark).cache()
    counts = {r["type"]: r["count"] for r in docs.groupBy("type").count().collect()}
    print("entities:", counts)

    index = f"{ALIAS}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    with open(MAPPING, encoding="utf-8") as f:
        es("PUT", f"/{index}", json.load(f))
    # es.mapping.id: 문서 id로 쓸 컬럼(같은 id면 덮어씀), exclude: 그 컬럼은 문서 본문에서 뺀다
    (docs.write.format("org.elasticsearch.spark.sql")
     .option("es.nodes", ES_URL.split("://")[-1].split(":")[0])
     .option("es.port", ES_URL.rsplit(":", 1)[-1] if ES_URL.count(":") > 1 else "9200")
     .option("es.nodes.wan.only", "true")
     .option("es.index.auto.create", "false")
     .option("es.mapping.id", "id")
     .option("es.mapping.exclude", "id")
     .mode("append").save(index))
    es("POST", f"/{index}/_refresh")  # 방금 쓴 문서를 검색·집계에 보이게 한다
    indexed = es("GET", f"/{index}/_count")["count"]
    if indexed != sum(counts.values()):
        raise RuntimeError(f"{index} has {indexed} documents, expected {sum(counts.values())}; alias not moved")

    # 별칭 추가와 이전 색인 삭제를 한 번의 요청으로(원자적으로) 처리한다
    old = [name for name in es("GET", f"/{ALIAS}-*/_alias") if name != index]
    actions = [{"add": {"index": index, "alias": ALIAS}}] + [{"remove_index": {"index": name}} for name in old]
    es("POST", "/_aliases", {"actions": actions})
    print(f"alias {ALIAS} -> {index} ({indexed} documents); removed {old or 'nothing'}")
    spark.stop()


if __name__ == "__main__":
    main()
