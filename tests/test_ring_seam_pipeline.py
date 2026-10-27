from types import SimpleNamespace

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal import pipeline


@pytest.mark.parametrize("shape,shape_code,channel", [
    ("圆形", "circle", "ring"),
    ("椭圆", "ellipse", "ring"),
    ("矩形", "rectangle", "body"),
])
def test_exact_cyclic_alignment_is_only_applied_to_ring_reads(
    monkeypatch, tmp_path, shape, shape_code, channel
):
    raw = "原市伊加壹电子服务总汇太"
    expected = "太原市伊加壹电子服务总汇"
    image_url = "/files/seal-input.png"
    prepared_input = {
        "id": f"seal-0:{channel}", "channel": channel, "image_url": image_url,
    }
    artifact = {
        "schema_version": 2, "index": 0, "region_id": "seal-0",
        "shape": shape, "shape_code": shape_code,
        "inputs": [prepared_input], "reads": [], "errors": [],
    }

    class Reader:
        def read(self, path, *, provider):
            return [TextObservation(raw, 0.68, 0.0, 0.0, 1.0, 1.0)]

        read_line = read

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    monkeypatch.setattr(
        pipeline, "prepare_inputs",
        lambda *args, **kwargs: (artifact, [(prepared_input, tmp_path / "seal-input.png")]),
    )

    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / "source.png", [], [SimpleNamespace()],
        tmp_path / "artifacts", "/files/artifacts", "paddle_v6",
        requirement=expected,
    )

    result = artifacts[0]
    assert result["errors"] == []
    assert result["reads"][0]["text"] == raw
    assert result["reads"][0]["confidence"] == 0.68
    assert result["reads"][0]["image_url"] == image_url
    assert result["inputs"][0]["image_url"] == image_url
    if channel == "ring":
        assert texts and set(texts) == {expected}
        assert result["unwrapped_raw_text"] == raw
        assert result["unwrapped_text"] == expected
        assert result["ring_reorder"]["reason"] == "exact_requirement_cyclic_reorder"
        assert compare_seal_text_strict(expected, texts)["status"] == "匹配"
    else:
        assert texts == [raw]
        assert "ring_reorder" not in result
        assert compare_seal_text_strict(expected, texts)["status"] == "不匹配"


@pytest.mark.parametrize("offset", [0, 14])
def test_warehouse_ring_keeps_company_and_centre_in_matching_order(monkeypatch, tmp_path, offset):
    from receipt_ocr.stages.seal import _round_stamp_candidates

    ring = "深圳欧瑞特供应链管理有限公司福州仓库"
    raw = ring[offset:] + ring[:offset]
    centre = "收货专用章(1)"
    prepared = {"id": "seal-0:ring", "channel": "ring", "image_url": "/ring.png"}
    artifact = {
        "schema_version": 2, "index": 0, "region_id": "seal-0",
        "shape": "圆形", "shape_code": "circle", "inputs": [prepared],
        "reads": [], "errors": [], "round_type_band_text": centre,
    }

    class Reader:
        def read_line(self, *args, **kwargs):
            return [TextObservation(raw, 1.0, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, "OcrReader", Reader)
    monkeypatch.setattr(pipeline, "prepare_inputs", lambda *a, **kw: (
        artifact, [(prepared, tmp_path / "ring.png")],
    ))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / "source.png", [], [SimpleNamespace()],
        tmp_path / "artifacts", "/files/artifacts", "paddle_v6",
        requirement=ring + centre,
    )

    assert artifacts[0]["reads"][0]["text"] == raw
    assert artifacts[0]["unwrapped_text"] == ring
    assert artifacts[0]["ring_reorder"]["changed"] is (offset != 0)
    if not offset:
        assert texts == [ring]  # No spurious reordered mismatch is added.
    candidates = _round_stamp_candidates(texts, artifacts)
    check = compare_seal_text_strict(ring + centre, candidates, mismatch_over_partial=True)
    assert check["status"] == "匹配"
    assert check["recognized"] == ring + centre
