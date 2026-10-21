import math

import pytest
from PIL import Image

from receipt_ocr.recognition.seal.preprocess.detection_scale import detect_orientation_boxes


def test_detect_at_bounded_size_and_map_polygon_to_full_stamp(monkeypatch, tmp_path):
    source = tmp_path / "stamp.png"
    Image.new("RGB", (1000, 1010), "white").save(source)
    calls = []

    def detect(path, **kwargs):
        with Image.open(path) as image:
            calls.append(image.size)
        return [{"text": "三星售后服务专用章", "confidence": .99,
                 "points": [[80, 300], [400, 250], [410, 300], [90, 350]],
                 "angle": -9, "x": .1, "y": .5}]

    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.detect_boxes", detect)
    boxes = detect_orientation_boxes(source, tmp_path, model_variant="v6")
    assert calls == [(507, 512)]
    assert boxes[0]["points"][0] == pytest.approx([80 * 1000 / 507, 300 * 1010 / 512])
    expected_angle = math.degrees(math.atan2(-50 * 1010 / 512, 320 * 1000 / 507))
    assert boxes[0]["angle"] == pytest.approx(expected_angle)
    with Image.open(source) as image:
        assert image.size == (1000, 1010)


def test_keeps_full_resolution_route_when_small_view_has_no_anchor(monkeypatch, tmp_path):
    source = tmp_path / "stamp.png"
    Image.new("RGB", (1000, 1010), "white").save(source)
    calls = []
    expected = [{"text": "专用章", "confidence": .99}]

    def detect(path, **kwargs):
        calls.append(path)
        return expected if path == source else []

    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.detect_boxes", detect)
    assert detect_orientation_boxes(source, tmp_path, model_variant="v6") == expected
    assert len(calls) == 2 and calls[-1] == source


def test_native_small_image_is_not_resampled(monkeypatch, tmp_path):
    source = tmp_path / "stamp.png"
    Image.new("RGB", (482, 487), "white").save(source)
    calls = []
    monkeypatch.setattr("receipt_ocr.recognition.seal.ocr.interface.detect_boxes",
                        lambda path, **kw: calls.append(path) or [])
    assert detect_orientation_boxes(source, tmp_path, model_variant="v6") == []
    assert calls == [source]
