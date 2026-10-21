from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import local_type_row
from receipt_ocr.recognition.seal.preprocess.inputs import _white_detected_type_region


def _reader(text="业务专用章", confidence=.99, *, touches_edge=False):
    def detect(path, **kwargs):
        # Search origin is (56, 90); this box recovers the missing first glyph.
        return [{"text": "业务专用章", "confidence": .99,
                 "points": [[0 if touches_edge else 24, 20], [204, 20], [204, 75], [24, 75]]}]
    return SimpleNamespace(detect_boxes=detect, read_line=lambda *a, **kw: [
        SimpleNamespace(text=text, confidence=confidence)])


def _sample(tmp_path):
    image = np.full((250, 350, 3), 255, np.uint8)
    for x in [85, 120, 155, 190, 225]:
        image[117:155, x:x + 20] = (30, 30, 250)
    image[135, 65:285] = (240, 240, 255)  # crossing faint line
    image[75:90, 150:170] = (30, 30, 250)  # adjacent artwork
    path = tmp_path / "source.png"
    cv2.imwrite(str(path), image)
    orientation = {"oriented_type_row_exact_box": [110, 100, 260, 170],
                   "oriented_type_row_box": [80, 90, 290, 170],
                   "type_band_text": "业务专用章", "anchor_text": "务专用章"}
    return path, image, orientation


def test_complete_local_polygon_is_shared_by_read_and_fill(tmp_path, monkeypatch):
    source, image, orientation = _sample(tmp_path)
    monkeypatch.setattr(local_type_row, "trace_ellipse_border", lambda *a, **kw: None)
    row = tmp_path / "row.png"
    proposal = local_type_row.prepare_local_type_row(source, row, orientation,
        reader=_reader(), provider="paddle_v6")
    assert proposal is not None
    assert proposal["polygon"][0] == [80, 110]
    assert proposal["text"] == "业务专用章"
    target = tmp_path / "masked.png"
    _white_detected_type_region(source, target, polygons=[proposal["polygon"]],
                               verified_coverage=proposal["coverage"])
    actual = cv2.imread(str(target))
    assert np.all(actual[117:155, 85:105] == 255)  # recovered leading glyph
    assert np.all(actual[117:155, 225:245] == 255)
    assert np.array_equal(actual[~proposal["coverage"]], image[~proposal["coverage"]])
    assert source.read_bytes() != target.read_bytes()
    np.testing.assert_array_equal(cv2.imread(str(source)), image)


@pytest.mark.parametrize("text,confidence,edge", [
    ("务专用章", .99, False), ("业务专用章", .7, False),
    ("业务专用章", float("nan"), False), ("业务专用章", .99, True),
])
def test_incomplete_unreliable_or_edge_clipped_read_does_not_change_geometry(
        tmp_path, monkeypatch, text, confidence, edge):
    source, _, orientation = _sample(tmp_path)
    monkeypatch.setattr(local_type_row, "trace_ellipse_border", lambda *a, **kw: None)
    assert local_type_row.prepare_local_type_row(source, tmp_path / "row.png", orientation,
        reader=_reader(text, confidence, touches_edge=edge), provider="paddle_v6") is None


def test_good_or_previously_verified_row_does_not_run_another_detector(tmp_path):
    source, _, orientation = _sample(tmp_path)
    def fail(*a, **kw):
        raise AssertionError("normal row should reuse existing geometry")
    reader = SimpleNamespace(detect_boxes=fail)
    for update in [{"horizontal_row_localization": {"method": "glyph_baseline"}},
                   {"oriented_type_row_exact_box": [40, 100, 300, 160]},
                   {"partial_anchor": True}]:
        assert local_type_row.prepare_local_type_row(source, tmp_path / "row.png",
            dict(orientation, **update), reader=reader, provider="paddle_v6") is None


def test_verified_fill_preserves_border_even_when_joined_to_the_row(tmp_path):
    image = np.full((120, 160, 3), 255, np.uint8)
    image[70:90, 40:120] = (20, 20, 250)
    image[80:100, 120:126] = (20, 20, 250)  # connected border
    image[45:55, 70:90] = (250, 250, 255)  # pale adjacent ink
    source, target = tmp_path / "source.png", tmp_path / "out.png"
    cv2.imwrite(str(source), image)
    coverage = np.zeros(image.shape[:2], bool)
    coverage[65:101, 35:120] = True
    _white_detected_type_region(source, target,
        polygons=[[[35, 65], [130, 65], [130, 100], [35, 100]]], verified_coverage=coverage)
    actual = cv2.imread(str(target))
    assert np.all(actual[70:90, 40:120] == 255)
    np.testing.assert_array_equal(actual[~coverage], image[~coverage])


def test_glyph_envelope_follows_tilt_and_excludes_star(tmp_path):
    image = np.full((600, 600, 3), 255, np.uint8)
    theta = np.linspace(0, 2 * np.pi, 720)
    guide = SimpleNamespace(points=np.column_stack((300 + 280 * np.cos(theta),
                                                    300 + 280 * np.sin(theta))),
                            annulus=[(None, None, ((300, 300), (560, 560), 0))])
    for x in [140, 210, 280, 350, 420]:
        y = round(470 - .12 * x)
        cv2.rectangle(image, (x, y - 85), (x + 40, y), (10, 10, 250), -1)
    cv2.circle(image, (300, 270), 45, (10, 10, 250), -1)
    polygon = np.array([[110, 250], [480, 230], [500, 480], [110, 500]], np.float32)
    result = local_type_row._glyph_envelope(image, polygon, 180, guide, 5)
    assert result is not None
    tight, count, angle = result
    assert count == 5 and -9 < angle < -4
    assert cv2.contourArea(tight) < cv2.contourArea(polygon)
    assert cv2.pointPolygonTest(tight, (300, 270), False) < 0
    assert local_type_row._glyph_envelope(image, polygon, 180, guide, 6) is None
