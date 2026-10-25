from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import type_row_mask
from receipt_ocr.recognition.seal.preprocess.inputs import _white_detected_type_region


def _sample(tmp_path, monkeypatch, color="red"):
    image = np.full((600, 600, 3), 255, np.uint8)
    ink = (20, 20, 250) if color == "red" else (250, 20, 20)
    cv2.circle(image, (300, 300), 270, ink, 5)
    for x in [115, 185, 255, 325, 395]:
        image[390:443, x:x + 42] = ink
    # A thin remnant joins the last two glyphs to the rim outside the polygon.
    image[415, 325:550] = ink
    image[220:255, 245:280] = ink  # star/number, outside the row
    image[445:460, 460:485] = (253, 253, 255)  # adjacent faint ring lettering
    theta = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    guide = SimpleNamespace(
        points=np.column_stack((300 + 270 * np.cos(theta), 300 + 270 * np.sin(theta))),
        annulus=[(None, None, ((300, 300), (540, 540), 0))],
        summary=lambda: {"method": "test_measured_rim"},
    )
    monkeypatch.setattr(type_row_mask, "trace_ellipse_border", lambda *a, **k: guide)
    source = tmp_path / "source.png"
    cv2.imwrite(str(source), image)
    return source, image, [[100, 380], [450, 380], [450, 455], [100, 455]], guide


@pytest.mark.parametrize("color", ["red", "blue"])
def test_thin_table_bridge_is_split_but_original_artwork_is_preserved(tmp_path, monkeypatch, color):
    source, image, polygon, guide = _sample(tmp_path, monkeypatch, color)
    evidence = {}
    target, mask_path = tmp_path / "ring.png", tmp_path / "mask.png"
    _white_detected_type_region(source, target, polygons=[polygon], mask_destination=mask_path,
        observed_text="业务专用章", observed_confidence=.99, color=color, mask_evidence=evidence)
    actual = cv2.imread(str(target))
    for x in [115, 185, 255, 325, 395]:
        assert np.all(actual[390:443, x:x + 42] == 255)
    roi = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(roi, [np.asarray(polygon)], 255)
    protected = np.zeros_like(roi)
    cv2.polylines(protected, [np.rint(guide.points).astype(np.int32)], True, 255, 12)
    np.testing.assert_array_equal(actual[(roi == 0) | (protected > 0)],
                                  image[(roi == 0) | (protected > 0)])
    np.testing.assert_array_equal(cv2.imread(str(source)), image)
    np.testing.assert_array_equal(cv2.imread(str(mask_path), 0) > 0,
                                  np.any(actual != image, axis=2))
    assert evidence["method"] == "verified_thin_bridge_glyphs"
    assert evidence["glyph_count"] == 5


@pytest.mark.parametrize("text,confidence,missing_guide", [
    ("业务专用章", .7, False), ("业务专用章", float("nan"), False),
    ("业务专用章", float("inf"), False), ("务专用章", .99, False),
    ("业务专用", .99, False), ("杭州松峰电子科技有限公司", .99, False),
    ("业务专用章", .99, True),
])
def test_uncertain_incomplete_or_unmeasurable_rows_keep_existing_mask(
        tmp_path, monkeypatch, text, confidence, missing_guide):
    source, _, polygon, _ = _sample(tmp_path, monkeypatch)
    if missing_guide:
        monkeypatch.setattr(type_row_mask, "trace_ellipse_border", lambda *a, **k: None)
    baseline, target = tmp_path / "old.png", tmp_path / "new.png"
    _white_detected_type_region(source, baseline, polygons=[polygon])
    evidence = {}
    _white_detected_type_region(source, target, polygons=[polygon], observed_text=text,
        observed_confidence=confidence, mask_evidence=evidence)
    assert not evidence
    np.testing.assert_array_equal(cv2.imread(str(baseline)), cv2.imread(str(target)))


def test_clean_row_reuses_existing_mask_without_tracing_border(tmp_path, monkeypatch):
    source, image, polygon, _ = _sample(tmp_path, monkeypatch)
    image[415, 437:550] = 255
    image[415, 367:395] = 255
    cv2.imwrite(str(source), image)

    def unexpected(*a, **k):
        raise AssertionError("a complete existing mask needs no new border fit")

    monkeypatch.setattr(type_row_mask, "trace_ellipse_border", unexpected)
    baseline, target = tmp_path / "old.png", tmp_path / "new.png"
    _white_detected_type_region(source, baseline, polygons=[polygon])
    evidence = {}
    _white_detected_type_region(source, target, polygons=[polygon], observed_text="业务专用章",
        observed_confidence=.99, mask_evidence=evidence)
    assert not evidence
    np.testing.assert_array_equal(cv2.imread(str(baseline)), cv2.imread(str(target)))
