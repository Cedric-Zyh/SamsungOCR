from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

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


def test_single_row_reads_exact_detector_extent_with_blank_padding(monkeypatch, tmp_path):
    source = tmp_path / "stamp.png"
    stamp = Image.new("RGB", (200, 200), "white")
    draw = ImageDraw.Draw(stamp)
    draw.rectangle((25, 85, 35, 115), fill="red")  # adjacent ring glyph
    draw.rectangle((40, 95, 45, 105), fill="black")  # leading row glyph
    stamp.save(source)
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
    assert result["oriented_type_row_read_box"] == result["oriented_type_row_exact_box"]
    assert result["oriented_type_row_box"][2] > result["oriented_type_row_read_box"][2]
    assert result["type_row_blank_padding"] == 6
    assert result["oriented_type_row_polygons"] == [polygon]
    with Image.open(result["type_band_path"]) as image:
        left, top, right, bottom = result["oriented_type_row_read_box"]
        assert image.size == (round(right) - round(left) + 12, round(bottom) - round(top) + 12)
        assert (255, 0, 0) not in image.getdata()
        assert image.getpixel((6, 11)) == (0, 0, 0)
        assert image.getpixel((0, 11)) == (255, 255, 255)
    # The evidence tile must show the same padded pixels used during rotation.
    from receipt_ocr.imaging.bands import save_round_seal_type_band
    center = save_round_seal_type_band(source, tmp_path / "center.png",
                                      focus_box=result["oriented_type_row_read_box"],
                                      blank_padding=result["type_row_blank_padding"])
    with Image.open(center) as image, Image.open(result["type_band_path"]) as verified:
        assert image.tobytes() == verified.tobytes()
