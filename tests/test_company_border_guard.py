"""A smooth, independently supported rim must not follow company glyphs."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.company_border import company_border_points
from receipt_ocr.recognition.seal.preprocess.multi_zone import _arc_maps
from receipt_ocr.recognition.seal.preprocess.continuous_company import continuous_company_pixels
from receipt_ocr.imaging.border_fit import _coordinates
from test_continuous_company_arc import arc_image


def damaged_guide(guide, *, span=36, depth=.12, method='observed_ellipse_border'):
    center = np.asarray(guide.annulus[0][2][0])
    delta = guide.points - center
    angles = np.rad2deg(np.mod(np.arctan2(delta[:, 1], delta[:, 0]), 2 * np.pi))
    dip = np.abs(angles - 215) < span / 2
    points = center + delta * np.where(dip, 1 - depth, 1)[:, None]
    return SimpleNamespace(annulus=guide.annulus, points=points,
                           summary=lambda: {'method': method})


@pytest.mark.parametrize('ratio', [.65, 1])
@pytest.mark.parametrize('color', ['red', 'blue'])
def test_company_strip_keeps_glyph_heads_after_local_border_dip(ratio, color):
    image, rho, actual = arc_image(range(140, 401, 15), ratio=ratio, color=color)
    damaged = damaged_guide(actual)
    before_image, before_points = image.copy(), damaged.points.copy()
    # Same measured coordinates, with the new correction deliberately disabled.
    old = SimpleNamespace(annulus=damaged.annulus, points=damaged.points,
                          summary=lambda: {'method': 'uncorrected_test'})
    old_map = _arc_maps(image, rho, old, allow_continuous_name=True)['company']
    selected = _arc_maps(image, rho, damaged, allow_continuous_name=True)['company']
    old_pixels = cv2.remap(image[:, :, 1], old_map['x'], old_map['y'], cv2.INTER_NEAREST)
    pixels = cv2.remap(image[:, :, 1], selected['x'], selected['y'], cv2.INTER_NEAREST)
    assert np.count_nonzero(old_pixels[0] < 100) > 0
    assert np.count_nonzero(pixels[0] < 100) == 0
    assert selected['border_adjustment']['method'] == 'robust_company_ellipse'
    np.testing.assert_allclose(selected['degrees'], old_map['degrees'], atol=.001)
    np.testing.assert_array_equal(image, before_image)
    np.testing.assert_array_equal(damaged.points, before_points)


def test_completed_reference_is_one_ellipse_and_rotation_is_supported():
    _, _, actual = arc_image(range(140, 401, 15), ratio=.65)
    guide = damaged_guide(actual)
    corrected, evidence = company_border_points(guide)
    center = np.asarray(guide.annulus[0][2][0])
    x, y = _coordinates(corrected, evidence['ellipse'])
    np.testing.assert_allclose(np.hypot(x, y), 1, atol=1e-10)
    assert evidence['supported_fraction'] > .8
    rotation = np.deg2rad(37)
    matrix = np.array([[np.cos(rotation), -np.sin(rotation)],
                       [np.sin(rotation), np.cos(rotation)]])
    outer = guide.annulus[0]
    rotated = SimpleNamespace(
        annulus=[(outer[0], outer[1], (tuple(center), outer[2][1], 37))],
        points=center + (guide.points - center) @ matrix.T, summary=guide.summary)
    result, audit = company_border_points(rotated)
    np.testing.assert_allclose(audit['ellipse'][0], evidence['ellipse'][0], atol=1e-4)
    np.testing.assert_allclose(audit['ellipse'][1], evidence['ellipse'][1], atol=1e-4)
    x, y = _coordinates(result, audit['ellipse'])
    np.testing.assert_allclose(np.hypot(x, y), 1, atol=1e-10)


@pytest.mark.parametrize('kind', ['broad_dip', 'estimate', 'missing_support'])
def test_insufficient_or_estimated_geometry_is_unchanged(kind):
    _, _, actual = arc_image(range(140, 401, 15))
    options = {'broad_dip': {'span': 110}, 'estimate': {'method': 'ellipse_estimate'},
               'missing_support': {}}[kind]
    guide = damaged_guide(actual, **options)
    if kind == 'missing_support':
        guide.supported = np.zeros(len(guide.points), bool)
    points, evidence = company_border_points(guide)
    assert points is guide.points
    assert evidence is None


def test_separate_upper_and_lower_rows_keep_their_existing_maps():
    image, rho, actual = arc_image(range(185, 356, 15), range(35, 146, 10))
    guide = damaged_guide(actual)
    old = SimpleNamespace(annulus=guide.annulus, points=guide.points,
                          summary=lambda: {'method': 'uncorrected_test'})
    before = _arc_maps(image, rho, old, allow_continuous_name=True)
    after = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(after) == {'upper', 'lower'}
    for key in before:
        np.testing.assert_array_equal(after[key]['x'], before[key]['x'])
        np.testing.assert_array_equal(after[key]['y'], before[key]['y'])
        assert 'border_adjustment' not in after[key]


def test_forward_company_fallback_uses_the_same_local_correction():
    image, _, actual = arc_image(range(140, 401, 15), ratio=1)
    pixels, geometry = continuous_company_pixels(image, damaged_guide(actual))
    assert geometry['border']['company_arc_correction']['method'] == 'robust_company_ellipse'
    assert np.count_nonzero(pixels[12] < 100) == 0


def test_smooth_reference_updates_radial_bounds_when_original_center_is_wrong():
    image, _, actual = arc_image(range(140, 401, 15), ratio=.75)
    # A broken outer contour gave the earlier fit a shifted, undersized frame.
    # The independently observed rim still describes the complete real ellipse.
    outer = actual.annulus[0]
    center, axes, angle = outer[2]
    wrong_center = (center[0] - 9, center[1] + 15)
    wrong_axes = tuple(v * .91 for v in axes)
    guide = SimpleNamespace(
        annulus=[(outer[0], outer[1], (wrong_center, wrong_axes, angle))],
        points=actual.points.copy(), summary=lambda: {'method': 'observed_ellipse_border'})
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx - wrong_center[0]) / (wrong_axes[0] / 2),
                   (yy - wrong_center[1]) / (wrong_axes[1] / 2))
    mapping = _arc_maps(image, rho, guide, allow_continuous_name=True)['company']
    np.testing.assert_allclose(mapping['border_adjustment']['ellipse'][0], center, atol=.001)
    pixels = cv2.remap(image[:, :, 1], mapping['x'], mapping['y'], cv2.INTER_NEAREST)
    assert np.count_nonzero(pixels[0] < 100) == 0
    assert np.count_nonzero(pixels[-1] < 100) == 0
