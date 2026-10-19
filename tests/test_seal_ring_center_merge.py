from types import SimpleNamespace

import pytest

from receipt_ocr.application.requests import StageRequest
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.stages import seal
from receipt_ocr.stages.seal import _ellipse_channels, _ellipse_display_texts, _round_stamp_candidates


def test_round_stamp_combines_ring_and_horizontal_type_channels():
    artifacts = [{
        "shape": "圆形",
        "unwrapped_text": "大连北华通信设备有限公司",
        "round_type_band_text": "售后专用章",
        "color_isolated_text": "",
    }]

    ring, type_text, _, _ = _ellipse_channels([], artifacts)

    assert ring == ["大连北华通信设备有限公司"]
    assert type_text == ["售后专用章"]
    assert _ellipse_display_texts([], artifacts) == [
        "大连北华通信设备有限公司",
        "售后专用章",
    ]


def test_rectangular_stamp_does_not_use_round_center_merge():
    artifacts = [{
        "shape": "矩形",
        "unwrapped_text": "公司",
        "round_type_band_text": "专用章",
    }]
    ring, type_text, _, _ = _ellipse_channels([], artifacts)
    assert ring == []
    assert type_text == []


@pytest.mark.parametrize("reverse", [False, True])
def test_complete_stamp_keeps_its_own_center_despite_faint_duplicate(reverse):
    company = "上海凝鹏通讯科技有限公司"
    artifacts = [
        {"shape": "椭圆", "round_type_band_text": "收货专用"},
        {"shape": "椭圆", "unwrapped_text": company, "round_type_band_text": "收货专用章"},
    ]
    candidates = _round_stamp_candidates(
        ["收货专用", "收货专用章", company], artifacts[::-1] if reverse else artifacts
    )
    result = compare_seal_text_strict(company + "收货专用章", candidates, mismatch_over_partial=True)
    assert result["recognized"] == company + "收货专用章"
    assert result["status"] == "匹配"
    assert company + "收货专用" not in candidates


def test_separate_stamps_cannot_supply_missing_channels_to_each_other():
    artifacts = [
        {"shape": "圆形", "unwrapped_text": "甲公司"},
        {"shape": "圆形", "round_type_band_text": "收货专用章(1)"},
    ]
    candidates = _round_stamp_candidates(["甲公司", "收货专用章(1)"], artifacts)
    assert "甲公司收货专用章(1)" not in candidates
    assert compare_seal_text_strict("甲公司收货专用章(1)", candidates)["status"] == "部分匹配"


def test_each_stamp_retains_its_number_and_incomplete_text():
    artifacts = [
        {"shape": "圆形", "unwrapped_text": "甲公司", "round_type_band_text": "收货专用章(2)"},
        {"shape": "圆形", "unwrapped_text": "乙公司", "round_type_band_text": "收货专用(1)"},
    ]
    candidates = _round_stamp_candidates([], artifacts)
    assert candidates == ["甲公司收货专用章(2)", "乙公司收货专用(1)"]
    assert compare_seal_text_strict("乙公司收货专用章(1)", candidates)["status"] == "部分匹配"


def test_local_stage_selects_complete_stamp_for_provider_evidence(monkeypatch, tmp_path):
    company = "上海凝鹏通讯科技有限公司"
    artifacts = [
        {"shape": "椭圆", "round_type_band_text": "收货专用"},
        {"shape": "椭圆", "unwrapped_text": company, "round_type_band_text": "收货专用章"},
    ]
    monkeypatch.setattr(seal, "_find_signature_requirement_row", lambda rows: SimpleNamespace(y=.6))
    monkeypatch.setattr(seal, "detect_seal_regions", lambda source: [])
    context = SimpleNamespace(
        source=tmp_path / "receipt.jpg", page=lambda backend: [],
        document_type={"type": "receipt"}, allows_remote_seal=False,
    )
    request = StageRequest(
        route={stage: "paddle_v6" for stage in ("page", "date", "seal")},
        fields={"签章要求": company + "收货专用章"},
    )
    result = seal.recognize(
        context, request,
        lambda *args, **kwargs: (["收货专用", "收货专用章", company], artifacts),
        SimpleNamespace(skipped=lambda: {"enabled": False}),
    )
    assert result.check["recognized"] == result.check["display_text"] == company + "收货专用章"
    assert result.check["status"] == "匹配"
    assert result.check["reliable"] is True
    assert result.artifacts == artifacts
