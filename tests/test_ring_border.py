"""Border geometry must follow visible ink, independently of OCR text."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.ring_border import trace_ellipse_border, save_ring_border_overlay
from receipt_ocr.imaging.unwrap import save_ring_text_band_preview, save_unwrapped_seal


def stamp(tmp_path, gap=0):
    image = np.full((500, 800, 3), 255, np.uint8)
    theta = np.linspace(0, 2 * np.pi, 1440, endpoint=False)
    ripple = 6 * np.sin(3 * theta) + 3 * np.cos(5 * theta)
    points = np.column_stack((400 + (340 + ripple) * np.cos(theta),
                              250 + (190 + ripple) * np.sin(theta))).round().astype(np.int32)
    border = np.zeros(image.shape[:2], np.uint8)
    cv2.polylines(border, [points], True, 255, 5)
    image[border > 0] = (70, 70, 220)
    cv2.putText(image, 'ABC XYZ', (240, 180), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (50, 50, 230), 4)
    if gap:
        # Remove a visible border arc, leaving its company-text interior alone.
        remove = np.zeros_like(border)
        cv2.ellipse(remove, (400, 250), (340, 190), 0, 0, gap, 255, 35)
        image[remove > 0] = 255
    source = tmp_path / 'seal.png'
    cv2.imwrite(str(source), image)
    return source, border


def test_border_guide_follows_non_elliptical_ink_and_preserves_source(tmp_path):
    source, border = stamp(tmp_path)
    before = source.read_bytes()
    guide = trace_ellipse_border(source)
    assert guide is not None
    distance = cv2.distanceTransform(255 - border, cv2.DIST_L2, 3)
    points = np.rint(guide.points).astype(int)
    error = distance[points[:, 1], points[:, 0]]
    assert np.quantile(error, .95) < 4
    assert guide.supported.mean() > .85
    assert guide.map_x.shape == guide.map_y.shape
    assert guide.map_x.shape[1] > guide.map_x.shape[0] * 8
    overlay = save_ring_border_overlay(source, tmp_path / 'overlay.png', guide)
    assert overlay.is_file()
    assert source.read_bytes() == before


def test_short_border_gap_is_marked_as_interpolated(tmp_path):
    source, _ = stamp(tmp_path, gap=30)
    guide = trace_ellipse_border(source)
    assert guide is not None
    assert .65 < guide.supported.mean() < .99


def test_half_missing_border_is_not_reported_as_a_measured_ring(tmp_path):
    source, _ = stamp(tmp_path, gap=200)
    assert trace_ellipse_border(source) is None


def test_preview_and_ocr_reject_a_guide_from_another_coordinate_frame(tmp_path):
    source, _ = stamp(tmp_path)
    guide = trace_ellipse_border(source)
    small = tmp_path / 'small.png'
    cv2.imwrite(str(small), np.full((100, 200, 3), 255, np.uint8))
    with pytest.raises(ValueError, match='坐标不一致'):
        save_ring_text_band_preview(small, tmp_path / 'preview.png', ring_guide=guide)
    with pytest.raises(ValueError, match='坐标不一致'):
        save_unwrapped_seal(small, tmp_path / 'line.png', None, elliptical=True,
                            direct_ellipse=True, single_line=True, ring_guide=guide)
