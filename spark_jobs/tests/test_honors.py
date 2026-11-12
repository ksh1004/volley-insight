"""curate_honors.py(선수별 수상·주요 기록 정제)의 순수 함수 테스트. Spark 없이 돈다: python -m pytest spark_jobs/tests"""
import sys
from pathlib import Path

# spark_jobs 폴더를 import 경로에 넣어 curate_honors 모듈을 바로 불러온다(pyspark는 main() 안에서만 불러온다)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curate_honors import honor_rows, split_line  # noqa: E402

# 실제 KOVO 응답(2026-11-11, /players/0000463/honor)에서 고른 문장들
PAYLOAD = {
    "awards": ["2005~2006 V-리그 신인선수상", "2005~2006 V-리그 월간 MVP (12월)"],
    "tripleCrowns": ["2005~2006 V-리그 역대 2호 흥국생명 vs GS칼텍스 (후위 10개, 서브 4개, 블로킹 3개)"],
    "standardRecords": ["2020~2021 V-리그 득점 3000개 달성 10호"],
}


def test_each_list_becomes_rows_of_its_kind_in_kovo_order():
    """세 목록이 각자의 종류(kind)로 펼쳐지고, seq는 KOVO 목록 안의 순서다."""
    rows = honor_rows("0000463", PAYLOAD)
    assert [(r["kind"], r["seq"], r["season_years"], r["title"]) for r in rows] == [
        ("AWARD", 0, "2005-2006", "신인선수상"),
        ("AWARD", 1, "2005-2006", "월간 MVP (12월)"),
        ("TRIPLE_CROWN", 0, "2005-2006", "역대 2호 흥국생명 vs GS칼텍스 (후위 10개, 서브 4개, 블로킹 3개)"),
        ("RECORD", 0, "2020-2021", "득점 3000개 달성 10호"),
    ]
    assert rows[0]["text"] == "2005~2006 V-리그 신인선수상"  # 원문은 그대로 남긴다


def test_lines_without_a_season_and_empty_payloads():
    """연도로 시작하지 않는 문장은 시즌 없이 원문 그대로, 한 해짜리 대회는 그 연도만, 빈 응답은 행이 없다."""
    assert split_line("  올스타전 MVP ") == ("", "올스타전 MVP")
    assert split_line("2018 KOVO컵(여자부) MIP") == ("2018", "KOVO컵(여자부) MIP")  # KOVO컵은 연도가 하나
    assert honor_rows("1", None) == [] and honor_rows("1", {"awards": [], "tripleCrowns": None}) == []
