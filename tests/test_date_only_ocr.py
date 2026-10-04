from receipt_ocr.recognition.date.evidence import (
    normalize_date_only_rows,
    normalize_date_only_text,
    sanitize_date_artifacts,
)
from receipt_ocr.domain.ocr import TextObservation


def test_date_line_ocr_uses_clean_views_and_keeps_raw_as_audit_only(tmp_path, monkeypatch):
    from receipt_ocr.providers import paddle_runtime
    from receipt_ocr.recognition.date.ocr.regions import (
        _recognize_region_lines,
    )
    from receipt_ocr.recognition.date.contracts import (
        DateCropRegion,
        DateCropRun,
        DateCropServices,
    )

    paths = {
        name: tmp_path / name
        for name in ("date-line-original.jpg", "date-line-clean.png", "date-line-frame.png")
    }
    calls = []

    def fake_recognize_line(path, *, model_variant):
        calls.append(path.name)
        return [TextObservation("2025年8月11日", 0.92, 0.0, 0.0, 1.0, 1.0)]

    monkeypatch.setattr(paddle_runtime, "recognize_line", fake_recognize_line)
    region = DateCropRegion("tight", True, 0.0, False)
    region.images.line_raw = paths["date-line-original.jpg"]
    region.images.line_color_clean = paths["date-line-clean.png"]
    region.images.line_positioned_frame_clean = paths["date-line-frame.png"]
    region.images.line_box = (0.0, 0.0, 1.0, 1.0)
    run = DateCropRun(
        source=tmp_path / "source.jpg",
        anchor_y=0.0,
        required_text="2025-08-11",
        artifact_dir=None,
        artifact_url_prefix="",
        ocr_backend="paddle_v6",
        allow_strict_date_without_requirement=False,
        creation_text="",
        temp_dir=tmp_path,
        services=DateCropServices(
            save_receipt_date_crop=lambda *args, **kwargs: None,
            save_date_line_crop=lambda *args, **kwargs: None,
            recognize_text=lambda *args, **kwargs: [],
            backend_label=lambda value: value,
        ),
    )

    _recognize_region_lines(run, region)

    assert calls == ["date-line-clean.png", "date-line-frame.png"]
    raw_variant = next(
        item for item in region.evidence.line_variants if item["preprocessing"] == "日期行原图"
    )
    assert raw_variant["ocr_texts"] == []
    assert [row.text for row in region.evidence.accepted_line_rows] == [
        "2025年8月11日",
        "2025年8月11日",
    ]


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
