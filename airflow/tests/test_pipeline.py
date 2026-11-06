"""Unit tests for airflow/dags/kovo_tasks/pipeline.py (no Airflow, Kafka, HDFS or database needed).

  .venv/Scripts/python -m pytest airflow/tests
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "airflow" / "dags"), str(ROOT / "collectors")]

from kovo_tasks import pipeline as p  # noqa: E402


def item(gnum, gdate, hs=3, as_=1, result="W"):
    return {"seasonCode": "023", "leagueCode": "201", "gnum": gnum, "hcode": "1006", "acode": "1009",
            "gdate": gdate, "hspoint": hs, "aspoint": as_, "result": result}


ITEMS = [
    item(1, "2026-10-31"),
    item(3, "2026-11-01", 1, 3, "L"),
    item(5, "2026-11-03", 0, 0, ""),     # not played yet
    item(7, "2026-11-03", 2, 1, ""),     # in progress
    item(8, "2026-11-03", 3, 2, "W"),
]


def test_pick_targets_only_finished_recent_unconfirmed():
    games = p.pick_targets(ITEMS, confirmed={1}, since=date(2026, 11, 1))
    assert [(g.key, g.sets) for g in games] == [("023-3", 4), ("023-8", 5)]


def test_pick_targets_limited_to_watched_games():
    assert [g.gnum for g in p.pick_targets(ITEMS, set(), since=date.min, only={8, 5})] == [8]


def test_parse_seasons():
    assert p.parse_seasons("1-3, 010") == ["001", "002", "003", "010"]
    assert p.parse_seasons("021") == ["021"]


def test_games_round_trip_through_xcom_dicts():
    games = p.pick_targets(ITEMS, set(), since=date.min)
    assert p.games_from_dicts(json.loads(json.dumps(p.games_to_dicts(games)))) == games


# ---------------------------------------------------------------- raw_arrived with a fake WebHDFS
class Resp:
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
    """dirs: {"/raw/topic=x/dt=d": [(name, mtime_ms, [lines...])]}"""

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
    return json.dumps({"endpoint": "/stat/real-time", "run_id": run_id, "i": i}, separators=(",", ":")).encode()


PUBLISHED = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)  # 21:00 KST
MS = int(PUBLISHED.timestamp() * 1000)
BATCH = {"topic": "kovo.rally", "run_id": "abc", "count": 3, "published_at": PUBLISHED.isoformat()}
DIR = "/raw/topic=kovo.rally/dt=2026-11-03"


def test_raw_arrived_counts_lines_of_the_run_in_finished_files():
    hdfs = FakeHdfs({DIR: [("part-1", MS + 60_000, [env("abc", 0), env("other"), env("abc", 1)]),
                           ("part-2", MS + 120_000, [env("abc", 2)])]})
    assert p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)


def test_raw_not_arrived_while_file_is_still_being_written():
    hdfs = FakeHdfs({DIR: [("part-1", MS + 60_000, [env("abc", 0), env("abc", 1)]),
                           (".part-2.inprogress", MS + 90_000, [env("abc", 2)]),
                           ("_SUCCESS", MS + 60_000, [])]})
    assert not p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)
    assert hdfs.opened == ["part-1"]


def test_raw_arrived_skips_files_older_than_the_run():
    hdfs = FakeHdfs({DIR: [("part-old", MS - 3_600_000, [env("abc")] * 3)]})
    assert not p.raw_arrived([BATCH], http=hdfs, now=PUBLISHED)
    assert hdfs.opened == []


def test_raw_arrived_looks_at_the_next_day_after_midnight_kst():
    late = datetime(2026, 11, 3, 14, 59, tzinfo=timezone.utc)  # 23:59 KST
    batch = {**BATCH, "published_at": late.isoformat()}
    ms = int(late.timestamp() * 1000)
    hdfs = FakeHdfs({DIR: [("part-1", ms + 30_000, [env("abc")])],
                     "/raw/topic=kovo.rally/dt=2026-11-04": [("part-2", ms + 120_000, [env("abc"), env("abc")])]})
    assert p.raw_arrived([batch], http=hdfs, now=datetime(2026, 11, 3, 15, 5, tzinfo=timezone.utc))


def test_empty_batches_need_no_wait():
    assert p.raw_arrived([{**BATCH, "count": 0}], http=FakeHdfs({}))


def test_quality_gate_fails_on_open_errors_and_missing_results():
    report = {"errors": [("023-5", 3, "MISSING_RALLY", "no play-by-play for this set"),
                         ("023-6", 0, "MISSING_BOX_SCORE", "no match detail collected")],
              "stale": [("023-7", "SCHEDULED")], "open": {"ERROR": 2, "WARNING": 9}}
    assert p.gate_problems(report) == [
        "023-5 set 3: MISSING_RALLY no play-by-play for this set",
        "023-6 set -: MISSING_BOX_SCORE no match detail collected",
        "023-7: started 6h+ ago, still SCHEDULED (result not collected)",
    ]


def test_quality_gate_passes_with_only_warnings():
    assert p.gate_problems({"errors": [], "stale": [], "open": {"WARNING": 9}}) == []


def test_parse_games_and_dates_for_reprocess():
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
