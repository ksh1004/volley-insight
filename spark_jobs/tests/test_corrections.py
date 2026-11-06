"""Correction keys of curate_kovo.py (ADM-03), no Spark needed: python -m pytest spark_jobs/tests

pyspark is only installed in the cluster images, so it is stubbed here; the tested helpers do not use it.
"""
import sys
from pathlib import Path
from unittest import mock

for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions", "pyspark.sql.types"):
    sys.modules.setdefault(name, mock.MagicMock())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curate_kovo import BLOCKING, correction_key, correction_plan  # noqa: E402


def test_keys_of_each_target():
    assert correction_key("player", " 1000525 ") == {"code": "1000525"}
    assert correction_key("match", "023-5") == {"season_code": "023", "game_no": 5}
    assert correction_key("set_score", "023-5/3") == {"season_code": "023", "game_no": 5, "set_no": 3}
    assert correction_key("player_match_stat", "023-5/0001328") == {"season_code": "023", "game_no": 5,
                                                                     "player_code": "0001328"}
    assert correction_key("team_match_stat", "022-163/1006")["team_code"] == "1006"


def test_malformed_keys_are_skipped():
    assert correction_key("match", "023-5/3") is None          # a sub key on a match
    assert correction_key("set_score", "023-5") is None        # set number missing
    assert correction_key("set_score", "023-5/x") is None
    assert correction_key("match", "23-5") is None
    assert correction_key("player_match_stat", "023/1000525") is None


def test_plan_groups_by_curated_table():
    plan = correction_plan([("match", "023-5", "spectators", "3120"), ("player", "0001328", "name", "홍길동"),
                            ("set_score", "bad", "home_points", "25"), ("unknown", "x", "y", "z"),
                            ("match", "023-6", "venue", None)])
    assert plan == {
        "match": [({"season_code": "023", "game_no": 5}, "spectators", "3120"),
                  ({"season_code": "023", "game_no": 6}, "venue", "")],
        "player": [({"code": "0001328"}, "name", "홍길동")],
    }


def test_blocking_rules_match_the_django_choices():
    text =(Path(__file__).resolve().parents[2] / "backend" / "ops" / "models.py").read_text(encoding="utf-8")
    errors = text.split("# ERROR:")[1].split("# WARNING:")[0]
    assert sorted(BLOCKING) == sorted(line.split("=")[0].strip() for line in errors.splitlines()[1:] if "=" in line)
