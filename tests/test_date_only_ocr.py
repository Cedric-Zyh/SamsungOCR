from receipt_ocr.date_fragments import (
    normalize_date_only_rows,
    normalize_date_only_text,
    sanitize_date_artifacts,
)
from receipt_ocr.ocr_types import TextObservation


def test_normalize_date_only_text_removes_non_date_words_and_normalizes_numeric_date():
    assert normalize_date_only_text("盖章 2026年2月6日") == "2026年2月6日"
    assert normalize_date_only_text("2026-02-06") == "2026年2月6日"
    assert normalize_date_only_text("2026年2月") == "2026年2月"
    assert normalize_date_only_text("专用章") == ""


def test_normalize_date_only_rows_preserves_geometry_and_confidence():
    row = TextObservation("盖章2026年2月", 0.81, 0.2, 0.3, 0.4, 0.1)
    normalized = normalize_date_only_rows([row])
    assert len(normalized) == 1
    assert normalized[0].text == "2026年2月"
    assert normalized[0].confidence == row.confidence
    assert normalized[0].x == row.x
    assert normalized[0].width == row.width


def test_sanitize_date_artifacts_restricts_all_date_ocr_variants():
    artifacts = [
        {
            "ocr_texts": ["盖章2026年2月6日", "专用章"],
            "decision_rows": [{"text": "日期2026-02-06", "confidence": 0.9}],
            "ocr_variants": [
                {"ocr_texts": ["签收2026年2月"], "accepted_texts": ["盖章2026年2月"]}
            ],
        }
    ]
    sanitized = sanitize_date_artifacts(artifacts)
    assert sanitized[0]["ocr_texts"] == ["2026年2月6日"]
    assert sanitized[0]["decision_rows"][0]["text"] == "2026年2月6日"
    assert sanitized[0]["ocr_variants"][0]["ocr_texts"] == ["2026年2月"]
    assert sanitized[0]["ocr_variants"][0]["accepted_texts"] == ["2026年2月"]
