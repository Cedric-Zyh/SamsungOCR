from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import row_extent
from receipt_ocr.recognition.seal.preprocess.inputs import _white_detected_type_region


def draw_row(path, scale=1):
    image = np.full((800, 800, 3), 255, np.uint8)
    cv2.circle(image, (400, 400), 365, (20, 20, 250), 8)
    for x in range(180, 650, 65):
        bottom = round(710 - .13 * (x + 22))
        cv2.rectangle(image, (x, bottom - 105), (x + 42, bottom), (20, 20, 250), 6)
    # A thin long line must not dictate the measured baseline.
    cv2.line(image, (150, 600), (680, 600), (20, 20, 250), 1)
    image = cv2.resize(image, None, fx=scale, fy=scale)
    cv2.imwrite(str(path), image)
    return [value * scale for value in [255, 560, 690, 665]]


@pytest.mark.parametrize('scale', [.7, 1, 1.5])
def test_clipped_row_is_measured_from_glyphs_at_different_scales(tmp_path, scale):
    path = tmp_path / 'row.png'
    seed = draw_row(path, scale)
    result = row_extent.measure_row_extent(path, seed)
    assert result is not None
    assert result['glyph_count'] >= 4
    assert abs(result['baseline_angle'] + 7.4) < 2
    polygon = np.array(result['polygon']) / scale
    assert polygon[0, 0] < 230  # recover the omitted leading glyph from ink
    assert polygon[1, 1] < 540  # recover the clipped upper-right strokes
    assert 'text' not in result  # geometry does not invent any characters


def test_no_baseline_or_no_border_does_not_guess_a_text_row(tmp_path):
    path = tmp_path / 'empty.png'
    cv2.imwrite(str(path), np.full((200, 200, 3), 255, np.uint8))
    assert row_extent.measure_row_extent(path, [20, 70, 180, 100]) is None


@pytest.mark.parametrize('text,confidence,accepted', [
    ('三星售后服务专用章', .98, True),
    ('三星售后服务专用章', .8, False),
    ('星售后服务专用章', .99, False),
    ('某某科技有限公司', .99, False),
    ('三星售后服务专用章', float('nan'), False),
])
def test_longer_read_must_verify_before_changing_mask(monkeypatch, tmp_path, text, confidence, accepted):
    source = tmp_path / 'row.png'
    seed = draw_row(source)
    proposal = row_extent.measure_row_extent(source, seed)
    assert proposal is not None
    reader = SimpleNamespace(read_line=lambda *a, **k: [SimpleNamespace(text=text, confidence=confidence)])
    result = row_extent.verify_row_extent(source, tmp_path / 'crop.png', seed,
        '星售后服务专用章', .95, reader=reader, provider='paddle_v6')
    assert (result is not None) == accepted


def test_verified_row_removes_joined_text_but_keeps_border_and_outside_pixels(tmp_path):
    source, output, mask_path = [tmp_path / name for name in ('source.png', 'out.png', 'mask.png')]
    image = np.full((100, 120, 3), 255, np.uint8)
    image[60:70, 40:80] = (20, 20, 250)  # text strokes
    image[65, 15:100] = (20, 20, 250)  # joined line crossing the row boundary
    image[84:90, 10:110] = (20, 20, 250)  # protected border
    image[40:50, 40:80] = (20, 20, 250)  # neighbouring text
    cv2.imwrite(str(source), image)
    _white_detected_type_region(source, output,
        polygons=[[[35, 55], [85, 55], [85, 95], [35, 95]]],
        verified_interior=[[10, 10], [110, 10], [110, 80], [10, 80]],
        mask_destination=mask_path)
    expected = image.copy(); expected[55:81, 35:86] = 255
    actual = cv2.imread(str(output))
    assert np.array_equal(actual, expected)
    assert np.array_equal(cv2.imread(str(mask_path), 0) > 0, np.any(actual != image, axis=2))


@pytest.mark.parametrize("coarse", [0, 90])
def test_complete_row_rotates_whole_stamp_before_final_read_and_maps_mask(monkeypatch, tmp_path, coarse):
    from receipt_ocr.recognition.seal.preprocess import combined_orientation as combined
    from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry
    source = tmp_path / "source.png"
    coarse_image = tmp_path / f"coarse-{coarse}.png"
    seed = draw_row(coarse_image)
    observed = cv2.imread(str(coarse_image))
    cv2.imwrite(str(source), observed if coarse == 0 else cv2.rotate(observed, cv2.ROTATE_90_CLOCKWISE))
    proposal = row_extent.measure_row_extent(coarse_image, seed)
    proposal.update(text="三星售后服务专用章", confidence=.98)
    points = [[seed[0], seed[1]], [seed[2], seed[1]],
              [seed[2], seed[3]], [seed[0], seed[3]]]
    best = {
        "path": coarse_image, "angle": coarse, "fine_rotation": 0,
        "fine_matrix": np.array([[1., 0, 0], [0, 1., 0]]),
        "candidate_width": 800, "candidate_height": 800,
        "final_width": 800, "final_height": 800,
        "boxes": [], "decision": {"anchor_points": points, "anchor_text": "星售后服务专用章"},
        "band_text": "星售后服务专用章", "band_confidence": .95,
    }
    events = []
    def verify(*args, **kwargs):
        events.append("read_complete_row")
        return dict(proposal)
    rotate = combined.rotate_stamp_image
    def rotate_whole(path, target, angle):
        events.append("rotate_whole_stamp")
        return rotate(path, target, angle)
    def read_final(path, target, **kwargs):
        events.append("read_rotated_stamp")
        assert cv2.imread(str(path)).shape[0] > 800  # expanded whole canvas, not a crop
        return "三星售后服务专用章", .98
    monkeypatch.setattr(combined, "verify_row_extent", verify)
    monkeypatch.setattr(combined, "rotate_stamp_image", rotate_whole)
    monkeypatch.setattr(combined, "_read_type_band_text", read_final)
    result = combined._orient_complete_row(source, best, tmp_path, tmp_path, model_variant="v6")
    assert events == ["read_complete_row", "rotate_whole_stamp", "read_rotated_stamp"]
    polygon = np.array(result["mask_polygons"][0])
    assert abs(polygon[0, 1] - polygon[1, 1]) < .02
    assert abs(polygon[2, 1] - polygon[3, 1]) < .02
    matrix, width, height = _rotation_geometry(800, 800, proposal["baseline_angle"])
    expected_image = cv2.warpAffine(observed, matrix, (width, height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    assert np.array_equal(cv2.imread(str(result["path"])), expected_image)
    expected_interior = cv2.transform(
        np.array([proposal["protected_interior"]], np.float32), matrix.astype(np.float32))[0]
    np.testing.assert_allclose(result["complete_row"]["protected_interior"], expected_interior, atol=.011)


def test_complete_row_keeps_old_candidate_if_final_stamp_read_changes(monkeypatch, tmp_path):
    from receipt_ocr.recognition.seal.preprocess import combined_orientation as combined
    source = tmp_path / "source.png"
    seed = draw_row(source)
    proposal = row_extent.measure_row_extent(source, seed)
    proposal.update(text="三星售后服务专用章", confidence=.98)
    best = {
        "path": source, "angle": 0, "fine_rotation": 0,
        "fine_matrix": np.array([[1., 0, 0], [0, 1., 0]]),
        "candidate_width": 800, "candidate_height": 800,
        "final_width": 800, "final_height": 800,
        "boxes": [], "decision": {"anchor_points": [
            [seed[0], seed[1]], [seed[2], seed[1]], [seed[2], seed[3]], [seed[0], seed[3]]]},
        "band_text": "星售后服务专用章", "band_confidence": .95,
    }
    monkeypatch.setattr(combined, "verify_row_extent", lambda *a, **k: dict(proposal))
    monkeypatch.setattr(combined, "_read_type_band_text", lambda *a, **k: ("错误文字", .99))
    before = source.read_bytes()
    assert combined._orient_complete_row(source, best, tmp_path, tmp_path, model_variant="v6") is best
    assert source.read_bytes() == before
