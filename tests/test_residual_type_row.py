from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import residual_type_row


def _sample(tmp_path, monkeypatch, *, merged=False, color="red", star_bridge=False):
    image = np.full((600, 600, 3), 255, np.uint8)
    ink = (20, 20, 250) if color == "red" else (250, 20, 20)
    cv2.circle(image, (300, 300), 270, ink, 5)
    positions = [135, 195, 255, 315, 375, 435]
    for x in positions:
        image[390:443, x:x + 42] = ink
    if merged:
        image[412:417, 135:477] = ink
    image[415, 477:550] = ink  # thin connection to the outer border
    image[395:445, 90:112] = ink  # neighbouring ring glyph outside the row
    image[220:255, 245:280] = ink
    if star_bridge:
        image[250:384, 253:260] = ink
        image[383:395, 255:258] = ink
    theta = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    guide = SimpleNamespace(
        source_shape=(600, 600),
        points=np.column_stack((300 + 270 * np.cos(theta), 300 + 270 * np.sin(theta))),
        annulus=[(None, None, ((300, 300), (540, 540), 0))],
        summary=lambda: {"method": "test_measured_rim"},
    )
    monkeypatch.setattr(residual_type_row, "trace_ellipse_border", lambda *a, **k: guide)
    source, target, mask = [tmp_path / name for name in ("source.png", "ring.png", "mask.png")]
    cv2.imwrite(str(source), image)
    cv2.imwrite(str(target), image)
    cv2.imwrite(str(mask), np.zeros(image.shape[:2], np.uint8))
    orientation = {"type_band_text": "业务专用章", "type_band_confidence": .99,
                   "oriented_type_row_read_box": [80., 375., 520., 460.]}
    arguments = dict(polygons=[[[120, 380], [490, 380], [490, 455], [120, 455]]],
                     orientation=orientation, geometry_source=source,
                     ring_guide=guide, color=color)
    return source, target, mask, image, positions, guide, arguments


@pytest.mark.parametrize("merged,color", [(True, "red"), (False, "red"), (True, "blue")])
def test_joined_row_does_not_need_one_component_per_ocr_character(tmp_path, monkeypatch, merged, color):
    source, target, mask, image, positions, guide, args = _sample(
        tmp_path, monkeypatch, merged=merged, color=color)
    result = residual_type_row.recover_residual_type_row(source, target, mask, **args)
    assert result is not None
    actual = cv2.imread(str(target))
    for x in positions:
        assert np.all(actual[390:443, x:x + 42] == 255)
    np.testing.assert_array_equal(actual[220:255, 245:280], image[220:255, 245:280])
    np.testing.assert_array_equal(actual[395:445, 90:112], image[395:445, 90:112])
    protected = np.zeros(image.shape[:2], np.uint8)
    cv2.polylines(protected, [np.rint(guide.points).astype(np.int32)], True, 255, 30)
    np.testing.assert_array_equal(actual[protected > 0], image[protected > 0])
    np.testing.assert_array_equal(cv2.imread(str(mask), 0) > 0, np.any(actual != image, axis=2))
    np.testing.assert_array_equal(cv2.imread(str(source)), image)
    assert result[1]["method"] == "verified_residual_row_glyphs"


def test_star_tip_is_not_used_as_top_of_verified_text_row(tmp_path, monkeypatch):
    source, target, mask, image, positions, _, args = _sample(
        tmp_path, monkeypatch, star_bridge=True)
    result = residual_type_row.recover_residual_type_row(source, target, mask, **args)
    assert result[1]["anchor_mode"] == "independent_observed_glyphs"
    actual = cv2.imread(str(target))
    np.testing.assert_array_equal(actual[:384], image[:384])
    for x in positions:
        assert np.all(actual[390:443, x:x + 42] == 255)


def test_small_remaining_part_is_found_without_whole_box_ratio(tmp_path, monkeypatch):
    source, target, mask, image, positions, _, args = _sample(tmp_path, monkeypatch)
    previous = image.copy()
    for x in positions[1:]:
        previous[390:443, x:x + 42] = 255
    cv2.imwrite(str(target), previous)
    result = residual_type_row.recover_residual_type_row(source, target, mask, **args)
    assert result is not None
    assert np.all(cv2.imread(str(target))[390:443, positions[0]:positions[0] + 42] == 255)


def test_already_clean_mask_keeps_files_and_skips_geometry(tmp_path, monkeypatch):
    source, target, mask, image, positions, _, args = _sample(tmp_path, monkeypatch)
    clean = image.copy()
    clean[380:456, 120:491] = 255
    cv2.imwrite(str(target), clean)
    before = target.read_bytes(), mask.read_bytes()

    def unexpected(*a, **k):
        raise AssertionError("clean inputs must not retrace the border")

    monkeypatch.setattr(residual_type_row, "trace_ellipse_border", unexpected)
    assert residual_type_row.recover_residual_type_row(source, target, mask, **args) is None
    assert (target.read_bytes(), mask.read_bytes()) == before


@pytest.mark.parametrize("changes", [
    {"type_band_confidence": .7}, {"type_band_confidence": float("nan")},
    {"type_band_confidence": float("inf")}, {"partial_anchor": True},
    {"type_band_text": "业务专用"}, {"type_band_text": "杭州松峰电子科技有限公司"},
])
def test_uncertain_or_non_type_text_cannot_enable_cleanup(tmp_path, monkeypatch, changes):
    source, target, mask, _, _, _, args = _sample(tmp_path, monkeypatch)
    args["orientation"].update(changes)
    before = target.read_bytes(), mask.read_bytes()
    assert residual_type_row.recover_residual_type_row(source, target, mask, **args) is None
    assert (target.read_bytes(), mask.read_bytes()) == before


def test_unmeasurable_border_keeps_existing_pixels(tmp_path, monkeypatch):
    source, target, mask, _, _, _, args = _sample(tmp_path, monkeypatch, merged=True)
    monkeypatch.setattr(residual_type_row, "trace_ellipse_border", lambda *a, **k: None)
    before = target.read_bytes(), mask.read_bytes()
    assert residual_type_row.recover_residual_type_row(source, target, mask, **args) is None
    assert (target.read_bytes(), mask.read_bytes()) == before
