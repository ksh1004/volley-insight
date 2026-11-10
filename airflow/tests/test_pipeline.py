"""airflow/dags/kovo_tasks/pipeline.py(DAG들이 함께 쓰는 수집·정제 단계 함수)의 단위 테스트.
Airflow·Kafka·HDFS·DB 없이 돈다.

  .venv/Scripts/python -m pytest airflow/tests

확인하는 것:
  - pick_targets: 일정 응답에서 "끝났고, 최근이고, 아직 확정 안 된" 경기만 고르는지
  - raw_arrived: Kafka로 보낸 원본이 Flink를 거쳐 HDFS 파일에 다 도착했는지 세는 방식
    (가짜 WebHDFS인 FakeHdfs로 폴더·파일·줄을 흉내 낸다)
  - gate_problems: 품질 관문이 미해결 오류와 결과 미수집 경기를 실패로 보는지
  - parse_games / parse_dates / merge_games: 재처리 DAG 입력값 해석
  - curation_running: 정제 DAG가 이미 돌고 있는지 Airflow API로 묻는지
  - title_key / resolve_stored: 뉴스 기사 제목 비교가 Flink 기사 보강 작업과 똑같은지
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# 저장소 최상위 폴더. DAG 폴더와 수집기 패키지를 import 경로 앞에 넣어 Airflow 없이 모듈을 불러온다.
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "airflow" / "dags"), str(ROOT / "collectors")]

from kovo_tasks import pipeline as p  # noqa: E402


def item(gnum, gdate, hs=3, as_=1, result="W"):
    """KOVO 일정 응답의 경기 한 줄. hs/as_는 홈/원정이 이긴 세트 수, result가 비면 아직 결과 없음."""
    return {"seasonCode": "023", "leagueCode": "201", "gnum": gnum, "hcode": "1006", "acode": "1009",
            "gdate": gdate, "hspoint": hs, "aspoint": as_, "result": result}


# 일정 예시: 확정된 1번, 끝난 3·8번, 아직 안 한 5번, 진행 중인 7번
ITEMS = [
    item(1, "2026-10-31"),
    item(3, "2026-11-01", 1, 3, "L"),
    item(5, "2026-11-03", 0, 0, ""),     # 아직 안 한 경기
    item(7, "2026-11-03", 2, 1, ""),     # 진행 중인 경기
    item(8, "2026-11-03", 3, 2, "W"),
]


def test_pick_targets_only_finished_recent_unconfirmed():
    """1번은 이미 확정, 5·7번은 안 끝남 → 3번(4세트)과 8번(5세트)만 대상이다."""
    games = p.pick_targets(ITEMS, confirmed={1}, since=date(2026, 11, 1))
    assert [(g.key, g.sets) for g in games] == [("023-3", 4), ("023-8", 5)]


def test_pick_targets_limited_to_watched_games():
    """only가 주어지면 그 경기들 중 끝난 것만 고른다(경기 후 DAG가 지켜보는 경기)."""
    assert [g.gnum for g in p.pick_targets(ITEMS, set(), since=date.min, only={8, 5})] == [8]


def test_parse_seasons():
    """"1-3, 010" 같은 입력을 세 자리 시즌 코드 목록으로 푼다."""
    assert p.parse_seasons("1-3, 010") == ["001", "002", "003", "010"]
    assert p.parse_seasons("021") == ["021"]


# ---------------------------------------------------------------- 가짜 WebHDFS로 raw_arrived 확인
class Resp:
    """requests 응답 흉내: JSON 본문, 상태 코드, 줄 단위 읽기(파일 내용)를 지원한다."""
    def __init__(self, status=200, body=None, lines=()):
        self.status_code, self._body, self._lines = status, body, list(lines)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHdfs:
    """WebHDFS 흉내. LISTSTATUS(폴더 목록)와 OPEN(파일 읽기)만 처리한다.

    dirs: {"/raw/topic=x/dt=d": [(파일 이름, 수정 시각 ms, [줄...])]}. 없는 폴더는 404.
    opened에는 실제로 열어 본 파일 이름이 쌓인다(불필요한 파일을 열지 않는지 확인용).
    """

    def __init__(self, dirs):
        self.dirs = dirs
        self.opened = []

    def get(self, url, params=None, stream=False, timeout=None):
        path = url.removeprefix(p.WEBHDFS)
        if params["op"] == "LISTSTATUS":
            if path not in self.dirs:
                return Resp(404)
            return Resp(body={"FileStatuses": {"FileStatus": [
                {"pathSuffix": n, "type": "FILE", "modificationTime": m} for n, m, _ in self.dirs[path]]}})
        directory, name = path.rsplit("/", 1)
        self.opened.append(name)
        return Resp(lines=next(lines for n, _, lines in self.dirs[directory] if n == name))


def env(run_id, i=0):
    """HDFS 원본 파일의 한 줄(수집기가 보낸 봉투 JSON). run_id로 어느 수집 실행의 것인지 구분한다."""
    return json.dumps({"endpoint": "/stat/real-time", "run_id": run_id, "i": i}, separators=(",", ":")).encode()


# 수집 실행 abc가 kovo.rally 토픽에 메시지 3개를 보낸 시각과 그 기록(BATCH), 그리고 그날의 HDFS 폴더
PUBLISHED = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)  # 한국 시각 21:00
MS = int(PUBLISHED.timestamp() * 1000)
BATCH = {"topic": "kovo.rally", "run_id": "abc", "count": 3, "published_at": PUBLISHED.isoformat()}
DIR = "/raw/topic=kovo.rally/dt=2026-11-03"


def test_raw_arrived_counts_lines_of_the_run_in_finished_files():
    """마감된 파일들에서 run_id가 abc인 줄만 세어 3개가 모이면 도착한 것이다(다른 실행의 줄은 무시)."""
    hdfs = FakeHdfs({DIR: [("part-1", MS + 60_000, [env("abc", 0), env("other"), env("abc", 1)]),
                           ("part-2", MS + 120_000, [env("abc", 2)])]})
    assert p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)


def test_raw_not_arrived_while_file_is_still_being_written():
    """.inprogress 파일(아직 쓰는 중)과 _SUCCESS 표시 파일은 열지 않는다 → 2개뿐이라 아직 미도착."""
    hdfs = FakeHdfs({DIR: [("part-1", MS + 60_000, [env("abc", 0), env("abc", 1)]),
                           (".part-2.inprogress", MS + 90_000, [env("abc", 2)]),
                           ("_SUCCESS", MS + 60_000, [])]})
    assert not p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)
    assert hdfs.opened == ["part-1"]


def test_raw_arrived_skips_files_older_than_the_run():
    """보낸 시각보다 먼저 마감된 파일에는 이번 실행의 줄이 있을 수 없으므로 열어 보지도 않는다."""
    hdfs = FakeHdfs({DIR: [("part-old", MS - 3_600_000, [env("abc")] * 3)]})
    assert not p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)
    assert hdfs.opened == []


def test_raw_arrived_looks_at_the_next_day_after_midnight_kst():
    """자정 직전에 보낸 메시지는 다음 날짜 폴더(dt=11-04)의 파일에 이어서 쓰일 수 있다."""
    late = datetime(2026, 11, 3, 14, 59, tzinfo=timezone.utc)  # 한국 시각 23:59
    batch = {**BATCH, "published_at": late.isoformat()}
    ms = int(late.timestamp() * 1000)
    hdfs = FakeHdfs({DIR: [("part-1", ms + 30_000, [env("abc")])],
                     "/raw/topic=kovo.rally/dt=2026-11-04": [("part-2", ms + 120_000, [env("abc"), env("abc")])]})
    assert p.raw_arrived([batch], http=hdfs, now=datetime(2026, 11, 3, 15, 5, tzinfo=timezone.utc))


def test_empty_batches_need_no_wait():
    """보낸 메시지가 0개면 기다릴 것도 없다(HDFS를 보지 않고 바로 도착 처리)."""
    assert p.raw_arrived([{**BATCH, "count": 0}], http=FakeHdfs({}))


def test_quality_gate_fails_on_open_errors_and_missing_results():
    """미해결 ERROR와, 시작한 지 6시간이 넘었는데 결과가 없는 경기를 사람이 읽을 문장으로 돌려준다."""
    report = {"errors": [("023-5", 3, "MISSING_RALLY", "no play-by-play for this set"),
                         ("023-6", 0, "MISSING_BOX_SCORE", "no match detail collected")],
              "stale": [("023-7", "SCHEDULED")], "open": {"ERROR": 2, "WARNING": 9}}
    assert p.gate_problems(report) == [
        "023-5 set 3: MISSING_RALLY no play-by-play for this set",
        "023-6 set -: MISSING_BOX_SCORE no match detail collected",
        "023-7: started 6h+ ago, still SCHEDULED (result not collected)",
    ]


def test_quality_gate_passes_with_only_warnings():
    """WARNING만 있으면 관문을 통과한다(경고는 운영 화면에서만 보여 준다)."""
    assert p.gate_problems({"errors": [], "stale": [], "open": {"WARNING": 9}}) == []


def test_parse_games_and_dates_for_reprocess():
    """재처리 입력: "시즌-경기번호" 목록(쉼표·공백 구분, 중복 제거)과 날짜/기간을 해석하고 잘못된 입력은 거절한다."""
    assert p.parse_games("023-9, 023-10 22-5 023-9") == {"022": [5], "023": [9, 10]}
    assert p.parse_games("") == {}
    for bad in ("023", "023-x", "a-1", "023-0"):
        try:
            p.parse_games(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert p.parse_dates("2026-11-05") == (date(2026, 11, 5), date(2026, 11, 5))
    assert p.parse_dates(" 2026-11-01 ~ 2026-11-05 ") == (date(2026, 11, 1), date(2026, 11, 5))
    for bad in ("2026-11-05~2026-11-01", "2026-10-01~2026-11-05", "11/05"):
        try:
            p.parse_dates(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert p.merge_games({"023": [9]}, {"023": [10, 9], "022": [1]}) == {"022": [1], "023": [9, 10]}


def test_curation_running_asks_airflow_for_each_curating_dag():
    """정제를 돌리는 DAG마다 queued/running 실행이 있는지 Airflow REST API로 묻고, 돌고 있는 DAG 이름을 돌려준다."""
    class Resp:
        def __init__(self, n):
            self.n = n

        def raise_for_status(self):
            pass

        def json(self):
            return {"total_entries": self.n}

    class Http:
        def __init__(self, running):
            self.running, self.asked = running, []

        def get(self, url, params, timeout):
            dag = url.split("/dags/")[1].split("/")[0]
            self.asked.append((dag, sorted(v for k, v in params if k == "state")))
            return Resp(1 if dag in self.running else 0)

    http = Http({"kovo_daily"})
    assert p.curation_running(http) == ["kovo_daily"]
    assert http.asked == [("kovo_daily", ["queued", "running"]), ("kovo_reprocess", ["queued", "running"])]
    assert p.curation_running(Http(set())) == []


def test_title_key_matches_the_article_enricher():
    """DAG의 제목 정규화(title_key)가 Flink 기사 보강 작업의 것과 같아야 같은 기사를 같은 것으로 본다."""
    sys.path.insert(0, str(ROOT / "flink_jobs"))
    from article_tags import title_key as enricher_key

    for t in ["[프로배구] 대한항공, 한국전력 꺾고 3연승 질주(종합)", "대한항공, 한국전력 꺾고 3연승 질주",
              "[포토] 김연경 스파이크", "[프로배구 전적] 3일", "", "OK저축은행 2연패 탈출 (2보)"]:
        assert p.title_key(t) == enricher_key(t), t


def test_resolve_stored_counts_an_article_merged_by_title():
    """다른 언론사의 같은 기사(제목이 같음)는 이미 저장된 기사로 합쳐진 것으로 센다. 제목이 다른 기사는 미저장."""
    urls = ["https://a.example/1", "https://b.example/9", "https://c.example/5"]
    titles = {"https://a.example/1": "[프로배구] 대한항공, 한국전력 꺾고 3연승 질주",
              "https://b.example/9": "대한항공, 한국전력 꺾고 3연승 질주(종합)",   # 같은 기사, 다른 언론사
              "https://c.example/5": "흥국생명 외국인 선수 교체 발표 예정"}           # 저장되지 않은 기사
    recent = [("https://a.example/1", titles["https://a.example/1"])]
    out = p.resolve_stored(urls, titles, {"https://a.example/1"}, recent)
    assert out == {"https://a.example/1": "https://a.example/1", "https://b.example/9": "https://a.example/1"}
