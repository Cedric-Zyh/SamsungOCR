import json

from tools.analyze_date_missing_year_separator import select_saved_candidate


def _result(*, confirmed: bool = True) -> dict:
    artifact = {
        "variant": "紧凑区域",
        "ocr_backend": "macOS Vision",
        "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
        "date_line_ocr_variants": [
            {
                "preprocessing": "日期行原图",
                "ocr_texts": ["20256月16日"],
            },
            {
                "preprocessing": "日期行去表格线",
                "ocr_texts": ["2025年6月6日"],
            },
            {
                "preprocessing": "日期行 Server 大模型复核",
                "ocr_texts": ["2025年6月16日"],
            },
        ],
    }
    if confirmed:
        artifact.update({
            "date_missing_year_separator_candidate": "2025-06-16",
            "date_slot_day_inner_reliable": True,
            "date_slot_day_inner_candidate": "16",
            "date_slot_day_inner_original_url": "/day-original.jpg",
            "date_slot_day_inner_processed_url": "/day-max.png",
            "date_slot_day_inner_line_clean_url": "/day-clean.png",
            "date_slot_day_inner_ocr_variants": [{
                "model": "mobile", "ocr_texts": ["16"]
            }],
        })
    return {
        "date_check": {"required": "2025-06-16"},
        "processing_artifacts": {"date": [artifact]},
    }


def test_saved_missing_year_separator_audit_distinguishes_prefilter_and_production():
    confirmed = select_saved_candidate(_result())
    assert confirmed["selected"] is True
    assert confirmed["confirmed"] is True
    assert confirmed["candidate"] == "2025-06-16"
    assert confirmed["other_dates"] == ["2025-06-06"]
    assert confirmed["artifact_urls"][
        "date_slot_day_inner_processed_url"
    ] == "/day-max.png"
    # Audit payloads must remain directly writable to the local JSON reports.
    json.dumps(confirmed, ensure_ascii=False)

    prefilter_only = select_saved_candidate(_result(confirmed=False))
    assert prefilter_only["selected"] is True
    assert prefilter_only["confirmed"] is False


def test_saved_missing_year_separator_audit_rejects_single_paddle_source():
    result = _result()
    result["processing_artifacts"]["date"][0]["ocr_backend"] = (
        "PaddleOCR PP-OCRv5 Mobile"
    )
    assert select_saved_candidate(result)["selected"] is False


def test_saved_missing_year_separator_audit_skips_already_reliable_old_route():
    result = _result(confirmed=False)
    result["date_check"]["reliable"] = True
    assert select_saved_candidate(result) == {
        "selected": False,
        "reason": "当前日期已由既有路线可靠确认",
    }
