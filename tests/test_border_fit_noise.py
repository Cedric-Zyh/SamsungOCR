"""Outside scan rules must not move the physical stamp ring or its baseline."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus, _save_ellipse_annulus_unwrapped


def border(axes, angle, noise):
    mask = np.zeros((900, 1100), np.uint8)
    cv2.ellipse(mask, (550, 450), axes, angle, 0, 360, 255, 8)
    if noise == 'top':
        cv2.line(mask, (40, 250), (1030, 50), 255, 4)
        cv2.line(mask, (100, 230), (1050, 65), 255, 3)
    elif noise == 'diagonal':
        for i in range(3):
            cv2.line(mask, (550+i*20, 10), (1080, 730+i*20), 255, 3)
    elif noise == 'detached':
        cv2.line(mask, (20, 30), (1080, 30), 255, 4)
    return mask


@pytest.mark.parametrize('axes,angle', [((350, 350), 0), ((420, 220), 0), ((370, 200), 27)])
@pytest.mark.parametrize('noise', ['top', 'diagonal', 'detached'])
def test_round_and_oval_geometry_excludes_outside_rules_without_editing_ink(axes, angle, noise):
    mask = border(axes, angle, noise)
    original = mask.copy()
    geometry = _fit_ellipse_annulus(mask)
    assert geometry is not None
    center, dimensions, _ = geometry[0][2]
    assert np.linalg.norm(np.subtract(center, (550, 450))) < 3
    assert np.max(np.abs(np.sort(dimensions)-np.sort(np.multiply(axes, 2)))) < 15
    assert np.array_equal(mask, original)


def test_corrected_circle_unwrap_has_a_level_baseline_and_keeps_every_glyph(tmp_path):
    mask = border((350, 350), 0, 'top')
    pixels = np.full_like(mask, 255)
    for angle in np.linspace(100, 420, 10):
        theta = np.deg2rad(angle)
        center = (round(550+265*np.cos(theta)), round(450+265*np.sin(theta)))
        cv2.circle(pixels, center, 7, 60, -1)
    before = pixels.copy()
    path = tmp_path/'unwrapped.png'
    assert _save_ellipse_annulus_unwrapped(pixels, mask, path, single_line=True)
    line = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    _, _, stats, centers = cv2.connectedComponentsWithStats((line < 150).astype(np.uint8), 8)
    glyphs = centers[1:][stats[1:, cv2.CC_STAT_AREA] > 5]
    assert len(glyphs) == 10
    assert np.ptp(glyphs[:, 1]) < 4
    assert np.array_equal(pixels, before)
