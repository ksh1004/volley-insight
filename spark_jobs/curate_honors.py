"""선수별 수상·주요 기록을 정제한다(COL-04): HDFS 원본 → player_honor 테이블(전체 교체).

  HDFS /raw/topic=kovo.player/   (수집기 HonorCollector의 봉투: /players/{코드}/honor 응답 원본)
    → 선수마다 가장 최근에 수집한 응답 하나
    → 수상(awards)·트리플크라운(tripleCrowns)·기준 기록 달성(standardRecords)의 문장을 한 줄씩 행으로
       (문장 앞의 "2025~2026 V-리그"에서 시즌을 떼어 season_years "2025-2026"으로, 나머지를 title로)
    → PostgreSQL player_honor (sql/merge_honors.sql, 한 트랜잭션으로 전체 교체)

KOVO 원문을 그대로 text에 남기고, 숫자·이름을 새로 만들거나 바꾸지 않는다(사실 데이터만, NFR-02).
매주 일부 선수(현역)만 다시 수집해도, HDFS에는 모든 선수의 마지막 응답이 남아 있으므로 표는 항상 전체가 다시 만들어진다.

실행(평소에는 Airflow DAG kovo_honors):
  spark-submit --master yarn --jars /opt/spark-jars/postgresql.jar --py-files spark_jobs/pg_merge.py \
    /opt/airflow/spark_jobs/curate_honors.py
"""
import os
import re
from pathlib import Path

RAW = os.environ.get("RAW_PATH", "hdfs://namenode:9000/raw")
MERGE_SQL = Path(__file__).resolve().parent / "sql" / "merge_honors.sql"

# KOVO 응답의 목록 이름 → player_honor.kind
KINDS = [("awards", "AWARD"), ("tripleCrowns", "TRIPLE_CROWN"), ("standardRecords", "RECORD")]
# 문장 앞의 시즌과 대회 이름: "2025~2026 V-리그 라운드 MVP (5R)" → ("2025", "2026", "라운드 MVP (5R)").
# KOVO컵은 한 해만 붙는다: "2014 KOVO컵 MIP" → ("2014", None, "KOVO컵 MIP")
SEASON_PREFIX = re.compile(r"^\s*(\d{4})(?:\s*~\s*(\d{4}))?\s*(?:V-리그\s*)?(.*)$")


def split_line(text: str):
    """문장 하나 → (season_years, title). season_years는 "2025-2026"(V-리그) 또는 "2014"(한 해짜리 대회).
    연도로 시작하지 않는 문장은 시즌 없이 원문 그대로."""
    m = SEASON_PREFIX.match(text)
    if not m:
        return "", text.strip()
    years = f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
    return years, m.group(3).strip() or text.strip()


def honor_rows(player_code: str, payload: dict | None) -> list:
    """선수 한 명의 응답 payload → player_honor 행들(dict). seq는 KOVO 목록 안의 순서(0부터, 오래된 것부터)."""
    rows = []
    for field, kind in KINDS:
        for seq, text in enumerate((payload or {}).get(field) or []):
            if not text or not text.strip():
                continue
            season_years, title = split_line(text)
            rows.append({"player_code": player_code, "kind": kind, "seq": seq, "season_years": season_years,
                         "title": title[:200], "text": text.strip()[:300]})
    return rows


# 자체 확인: 시즌 접두어 떼기, 시즌 없는 문장, 빈 목록
assert split_line("2025~2026 V-리그 라운드 MVP (5R)") == ("2025-2026", "라운드 MVP (5R)")
assert split_line("2020~2021 V-리그 득점 3000개 달성 10호") == ("2020-2021", "득점 3000개 달성 10호")
assert split_line("2014 KOVO컵 MIP") == ("2014", "KOVO컵 MIP")
assert split_line("올스타 MVP") == ("", "올스타 MVP")
assert honor_rows("1", {"awards": ["2005~2006 V-리그 신인선수상", " "], "tripleCrowns": None}) == [
    {"player_code": "1", "kind": "AWARD", "seq": 0, "season_years": "2005-2006", "title": "신인선수상",
     "text": "2005~2006 V-리그 신인선수상"}]

SCHEMA = "player_code STRING, kind STRING, seq INT, season_years STRING, title STRING, text STRING"
ENVELOPE = ("STRUCT<endpoint: STRING, params: MAP<STRING, STRING>, fetched_at: STRING, payload: STRUCT<payload: "
            "STRUCT<awards: ARRAY<STRING>, tripleCrowns: ARRAY<STRING>, standardRecords: ARRAY<STRING>>>>")


def main():
    """원본 읽기 → 선수별 최신 응답 → 행으로 펼치기(드라이버에서, 선수 약 1,100명이라 작다) → DB 전체 교체."""
    from pyspark.sql import SparkSession, Window
    from pyspark.sql import functions as F

    from pg_merge import load_and_merge

    spark = SparkSession.builder.appName("curate-honors").getOrCreate()
    lines = spark.read.text(f"{RAW}/topic=kovo.player")
    envelopes = (lines.where(F.get_json_object("value", "$.endpoint").rlike(r"^/players/[^/]+/honor$"))
                 .select(F.from_json("value", ENVELOPE).alias("m")).select("m.*")
                 .withColumn("player_code", F.col("params")["playerCode"]))
    latest = (envelopes.withColumn("_rn", F.row_number().over(
                  Window.partitionBy("player_code").orderBy(F.col("fetched_at").desc())))
              .where("_rn = 1").select("player_code", "payload.payload"))
    rows = [r for x in latest.collect() for r in honor_rows(x.player_code, x.payload.asDict() if x.payload else None)]
    players = latest.count()
    print(f"players with an honor page: {players}, honor lines: {len(rows)}")

    table = spark.createDataFrame(rows, SCHEMA)
    for row in load_and_merge(spark, {"player_honor": table}, MERGE_SQL, "merge_honors"):
        print("merge:", *row)
    spark.stop()


if __name__ == "__main__":
    main()
