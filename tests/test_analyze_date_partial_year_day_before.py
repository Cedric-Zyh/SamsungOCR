from tools.analyze_date_partial_year_day_before import select_saved_candidate


def _result(*, marker: bool = True, reliable: bool = False) -> dict:
    values = {
        ("mobile", "紧凑区域"): "200年8月19日",
        ("server", "紧凑区域"): "202年8月19日",
        ("mobile", "宽区域"): "20年8月19日",
        ("server", "宽区域"): "200年8月19日",
    }
    artifacts = [
        {
            "variant": geometry,
            "ocr_backend": "macOS Vision",
            "secondary_ocr_backend": "PaddleOCR PP-OCRv5 Mobile",
            "date_line_ocr_variants": [
                {
                    "preprocessing": (
                        "日期行最大通道去彩色三倍放大 "
                        f"{model.title()} 跨几何复核"
                    ),
                    "ocr_texts": [values[(model, geometry)]],
                }
                for model in ("mobile", "server")
            ],
            "date_partial_year_day_before_candidate": (
                "2025-08-19" if marker else ""
            ),
        }
        for geometry in ("紧凑区域", "宽区域")
    ]
    return {
        "fields": {"制单日期": "2025-08-16"},
        "date_check": {
            "required": "2025-08-20",
            "actual": "2025-08-19",
            "reliable": reliable,
            "partial_year_day_before_audit": {"candidate": "2025-08-19"},
        },
        "processing_artifacts": {"date": artifacts},
    }


def test_saved_partial_year_route_distinguishes_prefilter_and_marker():
    confirmed = select_saved_candidate(_result())
    assert confirmed["selected"] is True
    assert confirmed["candidate"] == "2025-08-19"
    assert confirmed["confirmed_review_suggestion"] is True

    prefilter_only = select_saved_candidate(_result(marker=False))
    assert prefilter_only["selected"] is True
    assert prefilter_only["confirmed_review_suggestion"] is False


def test_saved_partial_year_route_never_confirms_reliable_decision():
    audit = select_saved_candidate(_result(reliable=True))
    assert audit["selected"] is True
    assert audit["confirmed_review_suggestion"] is False
