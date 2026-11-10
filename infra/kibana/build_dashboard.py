"""dashboard.ndjson을 만든다: Kibana 데이터 뷰와 파이프라인 운영 대시보드(ADM-04, NFR-23).

  python infra/kibana/build_dashboard.py      # infra/kibana/dashboard.ndjson을 다시 씀
kibana-setup 서비스(compose.yaml)가 이 파일을 덮어쓰기로 가져오므로, 여기를 고치고 다시 돌리면 Kibana가 바뀐다.
(대시보드를 Kibana 화면에서 손으로 만들지 않고 코드로 만들어, 다시 설치해도 똑같이 재현되게 했다.)

패널들(출처: Filebeat logs-volley-*와 ops-collection-*, Metricbeat metrics-volley, Flink가 쓴 `articles`):
  작업별 수집 성공률, 실패한 실행, 시간별 수집 레코드 수     ← Filebeat가 옮긴 ops.collection-log
  컨슈머 그룹별 Kafka 지연                                  ← kafka-lag 서비스(Metricbeat 형식)
  Airflow DAG 실행(걸린 시간, 시작 지연)과 작업 대기 시간     ← Metricbeat sql 모듈
  서비스별 오류 로그 수 + 최근 오류 줄                       ← 컨테이너 로그
  규칙별 미해결 품질 문제, 지금 열린 오류 수                  ← Metricbeat sql 모듈(서비스 DB)
  언론사별 시간당 저장 기사 수                              ← Elasticsearch `articles`

용어: Lens = Kibana의 차트 편집기. 패널 하나 = Lens 시각화 객체 하나(열(columns) 정의 + 차트 종류).
"""
import json
from pathlib import Path

OUT = Path(__file__).with_name("dashboard.ndjson")
VERSION = "8.17.4"

# 데이터 뷰 id → (색인 패턴, 시간 필드, 화면 이름)
DATA_VIEWS = {
    "dv-logs": ("logs-volley-*", "@timestamp", "컨테이너 로그"),
    "dv-ops": ("ops-collection-*", "@timestamp", "수집 기록"),
    "dv-metrics": ("metrics-volley*", "@timestamp", "파이프라인 지표"),
    "dv-articles": ("articles", "collected_at", "기사"),
}


# ------------------------------------------------------------------ Lens 열(column) 정의 도우미
def date_hist(field="@timestamp"):
    """가로축용 시간 구간 열(자동 간격)."""
    return {"label": "시간", "dataType": "date", "operationType": "date_histogram", "sourceField": field,
            "isBucketed": True, "scale": "interval",
            "params": {"interval": "auto", "includeEmptyRows": True, "dropPartials": False}}


def terms(field, label, order_by, size=10):
    """값별로 나누는 열(예: 작업 이름별). order_by 열 기준 상위 size개."""
    return {"label": label, "dataType": "string", "operationType": "terms", "sourceField": field, "isBucketed": True,
            "scale": "ordinal", "customLabel": True,
            "params": {"size": size, "orderBy": {"type": "column", "columnId": order_by}, "orderDirection": "desc",
                       "otherBucket": True, "missingBucket": False, "parentFormat": {"id": "terms"}}}


def count(label="건수"):
    """문서 수를 세는 열."""
    return {"label": label, "dataType": "number", "operationType": "count", "isBucketed": False, "scale": "ratio",
            "sourceField": "___records___", "customLabel": True, "params": {"emptyAsNull": False}}


def metric(op, field, label, decimals=None):
    """필드에 집계 연산(op: average, max, sum 등)을 적용한 숫자 열."""
    col = {"label": label, "dataType": "number", "operationType": op, "sourceField": field, "isBucketed": False,
           "scale": "ratio", "customLabel": True, "params": {"emptyAsNull": True}}
    if decimals is not None:
        col["params"]["format"] = {"id": "number", "params": {"decimals": decimals}}
    return col


def last_value(field, label):
    """가장 최근 문서의 값을 보여 주는 열."""
    return {"label": label, "dataType": "number", "operationType": "last_value", "sourceField": field,
            "isBucketed": False, "scale": "ratio", "customLabel": True,
            "params": {"sortField": "@timestamp", "showArrayValues": False, "format": {"id": "number", "params": {"decimals": 0}}}}


def lens(obj_id, title, data_view, columns, visualization, vis_type, query="", description=""):
    """Lens 시각화 저장 객체 하나를 만든다(Kibana 가져오기 형식). id가 고정이라 다시 가져오면 덮어쓴다.
    columns: 순서 있는 {열 id: 열 정의}. 레이어는 항상 L1 하나."""
    return {
        "id": obj_id, "type": "lens", "managed": False, "typeMigrationVersion": "8.9.0", "coreMigrationVersion": VERSION,
        "attributes": {
            "title": title, "description": description, "visualizationType": vis_type,
            "state": {
                "datasourceStates": {"formBased": {"layers": {"L1": {
                    "columns": columns, "columnOrder": list(columns), "incompleteColumns": {}, "sampling": 1}}}},
                "visualization": visualization,
                "query": {"query": query, "language": "kuery"}, "filters": [],
                "internalReferences": [], "adHocDataViews": {},
            },
        },
        "references": [{"type": "index-pattern", "id": data_view, "name": "indexpattern-datasource-layer-L1"}],
    }


def xy(series, x, y, split=None, legend="right"):
    """막대/선 차트 설정(series: 'bar_stacked', 'line' 등, x/y/split: 열 id)."""
    layer = {"layerId": "L1", "layerType": "data", "seriesType": series, "xAccessor": x, "accessors": y, "position": "top",
             "showGridlines": False}
    if split:
        layer["splitAccessor"] = split
    return {"legend": {"isVisible": True, "position": legend}, "valueLabels": "hide", "fittingFunction": "None",
            "preferredSeriesType": series, "layers": [layer],
            "axisTitlesVisibilitySettings": {"x": False, "yLeft": True, "yRight": True}}


# ------------------------------------------------------------------ 패널들
PANELS = [
    lens("ops-success-rate", "수집 성공률 (작업별)", "dv-ops",
         {"job": terms("collection.job_name.keyword", "작업", "n"), "status": terms("collection.status.keyword", "결과", "n", 3),
          "n": count("실행 수")},
         xy("bar_horizontal_percentage_stacked", "job", ["n"], "status", "bottom"), "lnsXY",
         query='not collection.status.keyword : "RUNNING"',
         description="수집기 실행 결과의 SUCCESS/FAILED 비율 (Kafka ops.collection-log)"),
    lens("ops-failed", "수집 실패", "dv-ops", {"n": count("실패한 수집")},
         {"layerId": "L1", "layerType": "data", "metricAccessor": "n"}, "lnsMetric",
         query='collection.status.keyword : "FAILED"'),
    lens("ops-records", "수집 건수 추이", "dv-ops",
         {"t": date_hist(), "job": terms("collection.job_name.keyword", "작업", "n"),
          "n": metric("sum", "collection.record_count", "메시지 수", 0)},
         xy("bar_stacked", "t", ["n"], "job"), "lnsXY", query='collection.status.keyword : "SUCCESS"'),
    lens("kafka-lag", "Kafka 컨슈머 랙", "dv-metrics",
         {"t": date_hist(), "group": terms("kafka.consumergroup.id", "컨슈머 그룹", "lag"),
          "lag": metric("max", "kafka.consumergroup.consumer_lag", "랙 (메시지)", 0)},
         xy("line", "t", ["lag"], "group"), "lnsXY", query="metricset.name : \"consumergroup\"",
         description="처리하지 못하고 쌓인 메시지 수. raw-archiver·article-enricher는 체크포인트(30초)마다 위치를 커밋합니다."),
    lens("airflow-runs", "Airflow DAG 실행 (최근)", "dv-metrics",
         {"dag": terms("sql.metrics.dag_id", "DAG", "runs", 20), "runs": count("실행 수"),
          "dur": metric("average", "sql.metrics.duration_sec", "평균 소요 (초)", 1),
          "delay": metric("max", "sql.metrics.start_delay_sec", "최대 시작 지연 (초)", 1)},
         {"layerId": "L1", "layerType": "data", "columns": [{"columnId": c} for c in ("dag", "runs", "dur", "delay")]},
         "lnsDatatable", query='sql.metrics.kind : "dag_run"',
         description="시작 지연 = 예정 시각부터 실제 시작까지. 센서 대기(reschedule)는 소요 시간에 포함됩니다."),
    lens("airflow-queue", "Airflow 태스크 대기 시간", "dv-metrics",
         {"t": date_hist(), "q": metric("max", "sql.metrics.queue_delay_sec", "최대 대기 (초)", 1),
          "avg": metric("average", "sql.metrics.queue_delay_sec", "평균 대기 (초)", 1)},
         xy("line", "t", ["q", "avg"]), "lnsXY", query='sql.metrics.kind : "task"'),
    lens("logs-errors", "오류 로그 (서비스별)", "dv-logs",
         {"t": date_hist(), "svc": terms("compose_service", "서비스", "n"), "n": count("오류 줄 수")},
         xy("bar_stacked", "t", ["n"], "svc"), "lnsXY", query='log.level : "error"'),
    lens("articles-stored", "기사 저장 (언론사별)", "dv-articles",
         {"t": date_hist("collected_at"), "pub": terms("publisher", "언론사", "n"), "n": count("기사 수")},
         xy("bar_stacked", "t", ["n"], "pub"), "lnsXY",
         description="Flink article-enricher가 중복을 빼고 저장한 기사"),
    lens("quality-open", "데이터 품질: 미해결 건수 (검사별)", "dv-metrics",
         {"t": date_hist(), "rule": terms("sql.metrics.rule", "검사", "n", 12),
          "n": metric("max", "sql.metrics.issues", "건수", 0)},
         xy("bar_stacked", "t", ["n"], "rule"), "lnsXY",
         query='sql.metrics.kind : "quality" and sql.metrics.status : "OPEN"',
         description="정제 작업의 품질 검사(PIPE-10)에서 나온 미해결 항목. 확인함(원천 누락)은 빠집니다. Django 관리 > 품질 검사 결과"),
    lens("quality-errors", "미해결 품질 오류", "dv-metrics", {"n": last_value("sql.metrics.open_errors", "미해결 오류")},
         {"layerId": "L1", "layerType": "data", "metricAccessor": "n"}, "lnsMetric",
         query='sql.metrics.kind : "quality_total"',
         description="0이 아니면 경기가 기록 확정(CONFIRMED)되지 못하고, 다음 날 kovo_daily의 quality_gate가 실패합니다."),
]

ERROR_SEARCH = {
    "id": "logs-error-lines", "type": "search", "managed": False, "coreMigrationVersion": VERSION,
    "typeMigrationVersion": "10.5.0",
    "attributes": {
        "title": "최근 오류 로그", "description": "", "columns": ["compose_service", "message"],
        "sort": [["@timestamp", "desc"]], "grid": {}, "hideChart": True, "isTextBasedQuery": False,
        "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({
            "query": {"query": 'log.level : "error"', "language": "kuery"}, "filter": [],
            "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"})},
    },
    "references": [{"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern", "id": "dv-logs"}],
}

# 대시보드 배치: (패널 id, x, y, 폭, 높이). Kibana 대시보드는 가로 48칸 격자다
LAYOUT = [("ops-success-rate", 0, 0, 30, 12), ("ops-failed", 30, 0, 18, 12),
          ("ops-records", 0, 12, 24, 13), ("kafka-lag", 24, 12, 24, 13),
          ("airflow-runs", 0, 25, 24, 13), ("airflow-queue", 24, 25, 24, 13),
          ("logs-errors", 0, 38, 24, 13), ("articles-stored", 24, 38, 24, 13),
          ("quality-open", 0, 51, 30, 13), ("quality-errors", 30, 51, 18, 13),
          ("logs-error-lines", 0, 64, 48, 15)]


def dashboard():
    """패널들을 배치한 대시보드 저장 객체."""
    panels, refs = [], []
    types = {p["id"]: "lens" for p in PANELS} | {"logs-error-lines": "search"}
    for i, (pid, x, y, w, h) in enumerate(LAYOUT):
        idx = f"p{i}"
        panels.append({"type": types[pid], "panelIndex": idx, "gridData": {"x": x, "y": y, "w": w, "h": h, "i": idx},
                       "embeddableConfig": {"enhancements": {}}, "panelRefName": f"panel_{idx}"})
        refs.append({"name": f"{idx}:panel_{idx}", "type": types[pid], "id": pid})
    return {
        "id": "volley-ops", "type": "dashboard", "managed": False, "coreMigrationVersion": VERSION,
        "typeMigrationVersion": "10.2.0",
        "attributes": {
            "title": "배구 파이프라인 운영",
            "description": "수집 성공률, Kafka 컨슈머 랙, Airflow 지연, 오류 로그, 기사 수집 현황, 데이터 품질 (ADM-04, PIPE-10)",
            "panelsJSON": json.dumps(panels, ensure_ascii=False),
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": True, "syncCursor": True,
                                       "syncTooltips": False, "hidePanelTitles": False}),
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 60000},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []})},
        },
        "references": refs,
    }


def data_views():
    """데이터 뷰 저장 객체들."""
    for dv_id, (pattern, time_field, name) in DATA_VIEWS.items():
        yield {"id": dv_id, "type": "index-pattern", "managed": False, "coreMigrationVersion": VERSION,
               "typeMigrationVersion": "8.0.0",
               "attributes": {"title": pattern, "name": name, "timeFieldName": time_field}, "references": []}


def main():
    """모든 객체를 한 줄에 하나씩(NDJSON) 파일로 쓴다."""
    objects = [*data_views(), *PANELS, ERROR_SEARCH, dashboard()]
    OUT.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objects), encoding="utf-8")
    print(f"wrote {OUT} ({len(objects)} objects)")


if __name__ == "__main__":
    main()
