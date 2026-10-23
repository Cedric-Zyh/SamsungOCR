"""A blank printed requirement terminates only this receipt's local seal work."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from receipt_ocr.api.serializers import stage_result_payload
from receipt_ocr.application import plans
from receipt_ocr.application.assembly import _recognition_status
from receipt_ocr.application.requests import StageRequest
from receipt_ocr.domain.stages import STAGES
from receipt_ocr.stages import seal


@pytest.mark.parametrize("fields", [{}, {"签章要求": None}, {"签章要求": ""},
                                    {"签章要求": " \t\n\u3000"}])
def test_blank_requirement_stops_before_all_local_work(fields, monkeypatch):
    context = Mock()
    context.page.side_effect = AssertionError("must not read page")
    reader = Mock(side_effect=AssertionError("must not process local seal"))
    api = Mock()
    future = Mock()
    detector = Mock(side_effect=AssertionError("must not detect seal"))
    monkeypatch.setattr(seal, "detect_seal_regions", detector)

    result = seal.recognize(context, StageRequest({}, fields, seal_future=future), reader, api)

    assert result.check["execution_status"] == "skipped"
    assert result.check["skip_reason"] == "missing_requirement"
    assert result.check["recognized"] == ""
    assert result.check["reliable"] is False
    assert result.regions == result.artifacts == []
    assert context.mock_calls == reader.mock_calls == api.mock_calls == future.mock_calls == []
    detector.assert_not_called()


def test_empty_requirement_does_not_stop_remote_only_provider(monkeypatch, tmp_path):
    api = Mock()
    api.recognize.return_value = {"ok": False, "message": "no remote result"}
    monkeypatch.setattr(seal, "compare_qingtong_seal", lambda *a, **k: {"reliable": False})
    monkeypatch.setattr(seal, "qingtong_seal_regions", lambda *a: [])
    context = SimpleNamespace(source=tmp_path / "receipt.jpg",
                              document_type={"type": "receipt"}, allows_remote_seal=True)
    reader = Mock(side_effect=AssertionError("remote-only must not run local OCR"))
    route = dict.fromkeys(("page", "date", "seal"), "paddle_v6")

    result = seal.recognize(context, StageRequest(route, seal_mode="qingtong_only"), reader, api)

    api.recognize.assert_called_once_with(context.source)
    reader.assert_not_called()
    assert result.check["status"] == "识别失败"
    assert result.check.get("skip_reason") != "missing_requirement"


@pytest.mark.parametrize("requirement,local_calls", [("", 0), ("测试公司收货专用章", 1)])
def test_latest_fields_control_seal_work_without_stopping_date(
    requirement, local_calls, monkeypatch, tmp_path
):
    """The new blank field overrides stale fields; a following receipt still runs."""
    monkeypatch.setattr(seal, "_find_signature_requirement_row", lambda rows: SimpleNamespace(y=.6))
    monkeypatch.setattr(seal, "detect_seal_regions", lambda source: [])
    context = SimpleNamespace(source=tmp_path / "receipt.jpg", page=lambda backend: [],
                              primary_backend="paddle_v6", allows_remote_seal=False,
                              document_type={"type": "receipt", "reliable": True})
    reader = Mock(return_value=([requirement], []))
    seen = []

    def run_stage(context, stage, request):
        seen.append(stage)
        if stage == "fields":
            return {"fields": {"签章要求": requirement}}
        if stage == "date":
            return {"date_check": {"actual": "2026-02-25", "status": "匹配"}}
        return stage_result_payload(seal.recognize(context, request, reader, api))

    api = SimpleNamespace(skipped=lambda: {"enabled": False})
    analyzer = SimpleNamespace(seal_api=api, run_stage=run_stage)
    config = {stage: (["paddle_v6"] if stage in {"fields", "date", "seal"} else [])
              for stage in STAGES}
    results, errors, _, fields = plans._recognize_stages(
        analyzer, context, config, {"签章要求": "旧单据要求"}, {}
    )

    assert errors == []
    assert seen == ["fields", "date", "seal"]
    assert fields["签章要求"] == requirement
    assert results["date"][0]["result"]["date_check"]["actual"] == "2026-02-25"
    assert reader.call_count == local_calls
    check = results["seal"][0]["result"]["seal_check"]
    if not requirement:
        assert check["skip_reason"] == "missing_requirement"
        assert _recognition_status("seal", config["seal"], results["seal"]) == "已跳过（未提供签章要求）"
    else:
        assert check["recognized"] == requirement
        assert _recognition_status("seal", config["seal"], results["seal"]) == "已执行"


def test_skipped_local_variant_does_not_hide_executed_remote_variant():
    variants = [
        {"method": "paddle_v6", "result": {"seal_check": {
            "execution_status": "skipped", "skip_reason": "missing_requirement"}}},
        {"method": "qingtong", "result": {"seal_check": {"status": "缺少比对依据"}}},
    ]
    assert _recognition_status("seal", ["paddle_v6", "qingtong"], variants) == "已执行"
