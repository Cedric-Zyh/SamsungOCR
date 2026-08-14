from tools.analyze_date_white_day_conflicts import select_saved_candidate


def _result(*, marker: bool = True, reliable: bool = False) -> dict:
    artifacts = [
        {
            "variant": geometry,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_variants": [
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 Mobile 跨几何复核"
                    ),
                    "ocr_texts": ["2025年8月22月"],
                },
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 Server 跨几何复核"
                    ),
                    "ocr_texts": ["2025年8月23月"],
                },
            ],
            "date_white_day_audit_candidate": (
                "2025-08-22" if marker else ""
            ),
        }
        for geometry in ("紧凑区域", "宽区域")
    ]
    return {
        "date_check": {
            "required": "2025-08-23",
            "actual": "2025-08-22",
            "reliable": reliable,
            "white_day_conflict_audit": ({"candidate": "2025-08-22"}),
        },
        "processing_artifacts": {"date": artifacts},
    }


def test_saved_white_day_conflict_distinguishes_prefilter_and_review_marker():
    confirmed = select_saved_candidate(_result())
    assert confirmed["selected"] is True
    assert confirmed["candidate"] == "2025-08-22"
    assert confirmed["whole_line_conflict"] == "2025-08-23"
    assert confirmed["confirmed_review_suggestion"] is True

    prefilter_only = select_saved_candidate(_result(marker=False))
    assert prefilter_only["selected"] is True
    assert prefilter_only["confirmed_review_suggestion"] is False


def test_saved_white_day_conflict_never_confirms_reliable_decision():
    audit = select_saved_candidate(_result(reliable=True))
    assert audit["selected"] is True
    assert audit["confirmed_review_suggestion"] is False
