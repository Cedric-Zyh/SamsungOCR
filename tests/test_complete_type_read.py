from types import SimpleNamespace

import pytest
from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.type_row import prefer_complete_type_read
from receipt_ocr.recognition.seal.preprocess import combined_orientation


@pytest.mark.parametrize("current,alternative,confidence,expected", [
    ("收货专用", "收货专用章", .99, "收货专用章"),
    ("收貨专用", "收货专用章", .99, "收货专用章"),
    ("专用", "收货专用章", .99, "收货专用章"),
    ("收货专用章", "收货专用", .99, "收货专用章"),
    ("收货专用章", "收货专用章", .99, "收货专用章"),
    ("收货专用", "收货专用章", .4, "收货专用"),
    ("收货专用", "收货专用章", float("nan"), "收货专用"),
    ("收货专用", "业务专用章", .99, "收货专用"),
    ("收货专用", "收货专用有限公司", .99, "收货专用"),
])
def test_selects_complete_observation_without_inventing_or_overwriting_conflicts(
    current, alternative, confidence, expected,
):
    item = {"image_url": "/short.png", "verified_type_read": {
        "text": alternative, "confidence": confidence, "image_url": "/verified.png"}}
    artifact = {}
    rows = prefer_complete_type_read([TextObservation(current, .999, 0, 0, 1, 1)], item, artifact)
    assert "".join(row.text for row in rows) == expected
    if expected != current:
        assert artifact["type_row_selection"]["candidates"][0]["text"] == current
        assert artifact["round_type_band_url"] == "/verified.png"


def test_complete_type_read_reaches_final_texts_and_uses_its_own_image(monkeypatch, tmp_path):
    item = {"id": "seal-0:center", "channel": "center", "image_url": "/short.png",
            "verified_type_read": {"text": "收货专用章", "confidence": .9922,
                                   "image_url": "/verified.png"}}
    artifact = {"region_id": "seal-0", "shape_code": "round", "inputs": [item],
                "reads": [], "errors": []}
    monkeypatch.setattr(pipeline, "prepare_inputs", lambda *a, **k: (
        artifact, [(item, tmp_path / "short.png")]))
    monkeypatch.setattr(pipeline, "OcrReader", lambda: SimpleNamespace(
        read_line=lambda *a, **k: [TextObservation("收貨专用", .999, 0, 0, 1, 1)]))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / "source.png", [], [SimpleNamespace()], tmp_path / "out", "/files", "paddle_v6")
    assert texts == ["收货专用章"]
    assert artifacts[0]["round_type_band_text"] == "收货专用章"
    assert artifacts[0]["reads"][0]["image_url"] == "/verified.png"


def test_single_row_keeps_verified_read_extent_separate_from_ink_mask(monkeypatch, tmp_path):
    source = tmp_path / "stamp.png"
    Image.new("RGB", (200, 200), "white").save(source)
    polygon = [[40, 90], [120, 90], [120, 110], [40, 110]]
    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.detect_boxes",
                        lambda path, **k: ([{"text": "收货专用", "confidence": .99,
                                            "angle": 0, "points": polygon}]
                                          if path == source else []))
    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.read_line",
                        lambda *a, **k: [TextObservation("收货专用章", .99, 0, 0, 1, 1)])
    monkeypatch.setattr(combined_orientation, "_orient_complete_row", lambda source, best, *a, **k: best)
    _, result = combined_orientation.prepare_round_stamp_combined(source, tmp_path / "oriented.png")
    assert result["type_band_text"] == "收货专用章"
    assert result["oriented_type_row_read_box"] == result["oriented_type_row_box"]
    assert result["oriented_type_row_read_box"][2] > result["oriented_type_row_exact_box"][2]
    assert result["oriented_type_row_polygons"] == [polygon]
    with Image.open(result["type_band_path"]) as image:
        left, top, right, bottom = result["oriented_type_row_read_box"]
        assert image.size == (round(right) - round(left), round(bottom) - round(top))
