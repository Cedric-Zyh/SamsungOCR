import json
import sqlite3
from datetime import date

from tools.analyze_date_gaps import (
    _conflict_profile,
    _evidence_completeness,
    _parse_evidence_date,
    build_report,
)


def _write_result(database, filename, result):
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            original_result_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        "INSERT INTO results(filename,original_result_json) VALUES(?,?)",
        (filename, json.dumps(result, ensure_ascii=False)),
    )
    connection.commit()
    connection.close()


def test_date_evidence_completeness_distinguishes_full_and_damaged_years():
    assert _evidence_completeness("2025年8月7日") == "complete_four_digit_year"
    assert _evidence_completeness("2025年1218日") == "complete_four_digit_year"
    assert _evidence_completeness("20251218日") == "complete_four_digit_year"
    assert _evidence_completeness("202年8月7日") == "partial_year"
    assert _evidence_completeness("206月19日") == "malformed_compact_year"
    assert _evidence_completeness("6月19日") == "month_day_only"
    assert _evidence_completeness("12月19日") == "month_day_only"


def test_gap_parser_keeps_strict_year_that_differs_from_required():
    required = date(2025, 4, 26)
    assert _parse_evidence_date("2024年4月26日", required) == date(2024, 4, 26)
    assert _parse_evidence_date("202年4月26日", required) == required
    assert _parse_evidence_date("2025年1218日", required) == date(2025, 12, 18)
    assert _parse_evidence_date("订单20251218", required) is None


def test_conflict_profile_separates_cross_year_and_strict_conflicts():
    strict_truth = [{
        "engine": "server",
        "completeness": "complete_four_digit_year",
        "parsed": "2024-04-26",
    }]
    repaired_required = [{
        "engine": "mobile",
        "completeness": "month_day_only",
        "parsed": "2025-04-26",
    }]
    assert _conflict_profile(
        "2025-04-26", "2024-04-26", strict_truth, repaired_required
    ) == "跨年份严格真值与要求年份修复冲突"

    strict_other = [{
        "engine": "mobile",
        "completeness": "complete_four_digit_year",
        "parsed": "2025-04-02",
    }]
    assert _conflict_profile(
        "2025-04-26", "2025-04-26", strict_truth, strict_other
    ) == "完整日期相互冲突"


def test_date_gap_report_marks_legacy_and_current_preprocessing(tmp_path):
    database = tmp_path / "results.db"
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps(
            {
                "legacy.jpg": {
                    "fields": {"要求到货": "2025-08-07"},
                    "actual_date": "2025-08-07",
                },
                "current.jpg": {
                    "fields": {"要求到货": "2025-06-20"},
                    "actual_date": "2025-06-19",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    base = {
        "ocr_backend": "hybrid",
        "date_check": {"actual": "", "status": "未识别", "reliable": False},
    }
    _write_result(
        database,
        "legacy.jpg",
        {
            **base,
            "processing_artifacts": {
                "date": [{
                    "variant": "宽区域",
                    "date_line_original_url": "/legacy.jpg",
                    "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
                    "date_line_ocr_variants": [],
                }]
            },
        },
    )
    _write_result(
        database,
        "current.jpg",
        {
            **base,
            "processing_artifacts": {
                "date": [{
                    "variant": "紧凑区域",
                    "date_line_original_url": "/current.jpg",
                    "date_line_table_clean_url": "/current-clean.png",
                    "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
                    "date_line_ocr_variants": [{
                        "preprocessing": "日期行去表格线",
                        "ocr_texts": ["206月19日"],
                    }],
                }]
            },
        },
    )

    report = build_report(database, truth_path, "hybrid")
    by_name = {row["filename"]: row for row in report["samples"]}

    assert by_name["legacy.jpg"]["gap_category"] == "旧版中间图无可解析候选"
    assert by_name["legacy.jpg"]["has_table_clean_artifact"] is False
    assert by_name["current.jpg"]["gap_category"] == "无冲突单引擎残缺候选"
    assert by_name["current.jpg"]["truth_evidence"][0]["completeness"] == (
        "malformed_compact_year"
    )
    assert report["gap_categories"] == {
        "旧版中间图无可解析候选": 1,
        "无冲突单引擎残缺候选": 1,
    }


def test_date_gap_report_counts_cross_model_slot_candidate(tmp_path):
    database = tmp_path / "results.db"
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps({
            "slot.jpg": {
                "fields": {"要求到货": "2025-08-27"},
                "actual_date": "2025-08-27",
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_result(database, "slot.jpg", {
        "ocr_backend": "hybrid",
        "date_check": {
            "actual": "2025-08-27",
            "status": "匹配",
            "reliable": False,
        },
        "processing_artifacts": {"date": [{
            "variant": "紧凑区域",
            "date_line_table_clean_url": "/table.png",
            "date_slot_candidate": "2025-08-27",
            "date_slot_year_processed_url": "/year.png",
            "date_slot_month_day_processed_url": "/month-day.png",
        }]},
    })

    report = build_report(database, truth_path, "hybrid")
    sample = report["samples"][0]

    assert report["summary"]["truth_candidate_present"] == 1
    assert report["summary"]["truth_across_two_engines"] == 1
    assert report["summary"].get("no_parseable_candidate", 0) == 0
    assert sample["gap_category"] == "无冲突完整年份候选"
    assert sample["truth_engines"] == ["mobile", "server"]
    assert sample["date_artifact_urls"][0]["slot_year_url"] == "/year.png"


def test_date_gap_report_keeps_vision_slot_as_composite_review_evidence(
    tmp_path,
):
    database = tmp_path / "results.db"
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps({
            "slot.jpg": {
                "fields": {"要求到货": "2025-08-24"},
                "actual_date": "2025-08-24",
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_result(database, "slot.jpg", {
        "ocr_backend": "hybrid",
        "date_check": {
            "actual": "2025-08-24",
            "status": "匹配",
            "reliable": False,
        },
        "processing_artifacts": {"date": [{
            "variant": "紧凑区域",
            "date_line_table_clean_url": "/table.png",
            "date_slot_candidate": "2025-08-24",
            "date_slot_candidate_source": (
                "Paddle 双模型年份 + Vision 单路径月日"
            ),
            "date_slot_year_white_url": "/year-white.png",
            "date_slot_month_day_vision_url": "/month-day-vision.png",
        }]},
    })

    report = build_report(database, truth_path, "hybrid")
    sample = report["samples"][0]

    assert report["summary"]["truth_candidate_present"] == 1
    assert report["summary"].get("truth_across_two_engines", 0) == 0
    assert sample["truth_engines"] == ["hybrid_slot"]
    assert sample["truth_strict_engines"] == []
    assert sample["truth_evidence"][0]["backend"] == (
        "Paddle 双模型年份 + macOS Vision 月日"
    )
    urls = sample["date_artifact_urls"][0]
    assert urls["slot_year_white_url"] == "/year-white.png"
    assert urls["slot_month_day_vision_url"] == "/month-day-vision.png"


def test_date_gap_report_attributes_white_line_variant_to_vision(tmp_path):
    database = tmp_path / "results.db"
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(
        json.dumps({
            "white.jpg": {
                "fields": {"要求到货": "2025-03-07"},
                "actual_date": "2025-03-07",
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_result(database, "white.jpg", {
        "ocr_backend": "hybrid",
        "date_check": {
            "actual": "2025-03-07",
            "status": "匹配",
            "reliable": False,
        },
        "processing_artifacts": {"date": [{
            "variant": "紧凑区域",
            "date_line_table_clean_url": "/table.png",
            "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_variants": [{
                "preprocessing": (
                    "原日期行白底标准化 Vision 中英关闭纠错 人工候选"
                ),
                "ocr_texts": ["_2025年3月7E"],
            }],
        }]},
    })

    report = build_report(database, truth_path, "hybrid")
    sample = report["samples"][0]

    assert sample["truth_engines"] == ["vision"]
    assert sample["truth_evidence"][0]["backend"] == "macOS Vision"
