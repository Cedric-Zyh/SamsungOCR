"""Tests for the new typed stage-to-JSON application boundary."""

from types import SimpleNamespace

from receipt_ocr.api.serializers import stage_result_payload
from receipt_ocr.application import pipeline
from receipt_ocr.domain.results import (
    DateStageResult,
    FieldStageResult,
    SealStageResult,
)


def test_field_result_serialization_keeps_stage_owned_metadata_and_artifacts():
    result = FieldStageResult(
        fields={"客户名称": "测试客户"},
        metadata={"客户名称": {"confidence": .98}},
        fallbacks={},
        qr_text="QR-1",
        requirement_artifacts=[{"url": "/files/a.png"}],
        review_reasons=["字段低置信度"],
    )

    assert stage_result_payload(result) == {
        "fields": {"客户名称": "测试客户"},
        "field_metadata": {"客户名称": {"confidence": .98}},
        "field_fallbacks": {},
        "qr_text": "QR-1",
        "processing_artifacts": {"signature_requirement": [{"url": "/files/a.png"}]},
        "stage_review_reasons": ["字段低置信度"],
    }


def test_stage_runner_serializes_typed_result_at_application_boundary(monkeypatch):
    typed = DateStageResult(
        check={"status": "匹配", "actual": "2026-09-23"},
        ocr_texts=["2026年9月23日"],
        artifacts=[{"variant": "紧凑区域"}],
        preview_box=(.1, .2, .3, .04),
        review_reasons=[],
    )
    monkeypatch.setattr(pipeline, "recognize_stage", lambda *args: typed)
    context = SimpleNamespace(evidence=lambda: {"document_type": {"type": "receipt"}})

    result = pipeline.execute_stage(object(), context, "date", object())

    assert result["document_type"]["type"] == "receipt"
    assert result["date_check"] == typed.check
    assert result["processing_artifacts"]["date"] == typed.artifacts
    assert result["preview_date_box"] == typed.preview_box


def test_empty_result_still_has_every_external_stage_slot():
    context = SimpleNamespace(evidence=lambda: {})
    result = pipeline.empty_result(context)

    assert set(("fields", "product_table", "date_check", "seal_check")) <= set(result)
    assert set(result["processing_artifacts"]) == {"date", "seals"}
