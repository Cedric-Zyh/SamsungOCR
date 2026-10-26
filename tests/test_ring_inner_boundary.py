"""An internal ink contour must not truncate otherwise complete ring glyphs."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging import ellipse, unwrap


def fixture(count=6, *, inner_fraction=.68, estimated=False, scale=1):
    center = np.array([250., 250.]) * scale
    radius = 200 * scale
    mask = np.zeros((round(500 * scale), round(500 * scale)), np.uint8)
    cv2.circle(mask, tuple(center.astype(int)), round(radius), 255, max(1, round(3 * scale)))
    glyphs = np.zeros_like(mask)
    for angle in np.linspace(190, 340, count):
        theta = np.deg2rad(angle)
        radial = np.array([np.cos(theta), np.sin(theta)])
        tangent = np.array([-np.sin(theta), np.cos(theta)])
        points = [center + radius * fraction * radial + halfwidth * scale * tangent
                  for fraction, halfwidth in ((.90, -10), (.90, 10), (.59, 10), (.59, -10))]
        cv2.fillPoly(glyphs, [np.rint(points).astype(np.int32)], 255)
    mask |= glyphs
    outer = (radius * 2, 0., (tuple(center), (radius * 2, radius * 2), 0.))
    inner = (radius * 2 * inner_fraction, 0.,
             (tuple(center), (radius * 2 * inner_fraction, radius * 2 * inner_fraction), 0.))
    return mask, glyphs, (outer, inner, estimated)


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_cut_glyphs_keep_their_inner_ends(scale):
    mask, glyphs, geometry = fixture(scale=scale)
    old = ellipse._ellipse_normal_maps(*geometry)
    selected = ellipse._ring_text_normal_maps(mask, geometry)
    old_band = cv2.remap(glyphs, *old, cv2.INTER_NEAREST)
    new_band = cv2.remap(glyphs, *selected, cv2.INTER_NEAREST)
    assert np.count_nonzero(old_band[-1]) > 0  # The old band ends inside text.
    assert np.count_nonzero(new_band[-1]) == 0
    center = np.array(geometry[0][2][0])
    old_inner_radius = np.hypot(old[0][-1] - center[0], old[1][-1] - center[1]).min()
    new_radius = np.hypot(selected[0] - center[0], selected[1] - center[1])
    # Compare source coverage rather than raw pixel counts: small stamps have
    # a minimum output height, so the two bands have different radial scales.
    assert np.count_nonzero(new_band[new_radius < old_inner_radius - 3 * scale]) > 0


@pytest.mark.parametrize('count', [0, 1, 2])
def test_noise_or_few_edge_components_do_not_expand_the_band(count):
    mask, _, geometry = fixture(count=count)
    expected = ellipse._ellipse_normal_maps(*geometry)
    actual = ellipse._ring_text_normal_maps(mask, geometry)
    assert all(np.array_equal(a, b) for a, b in zip(actual, expected))


def test_complete_text_and_real_inner_border_keep_existing_geometry():
    mask, _, geometry = fixture(inner_fraction=.40)
    cv2.circle(mask, (250, 250), 80, 255, 3)
    expected = ellipse._ellipse_normal_maps(*geometry)
    actual = ellipse._ring_text_normal_maps(mask, geometry)
    assert all(np.array_equal(a, b) for a, b in zip(actual, expected))


def test_single_border_estimate_is_unchanged():
    mask, _, geometry = fixture(estimated=True)
    expected = ellipse._ellipse_normal_maps(*geometry)
    actual = ellipse._ring_text_normal_maps(mask, geometry)
    assert all(np.array_equal(a, b) for a, b in zip(actual, expected))


def test_preview_and_ocr_keep_the_same_full_glyph_band(monkeypatch, tmp_path):
    mask, glyphs, geometry = fixture()
    source = np.full((*mask.shape, 3), 255, np.uint8)
    source[mask > 0] = (30, 30, 230)
    path = tmp_path / 'source.png'
    cv2.imwrite(str(path), source)
    monkeypatch.setattr(unwrap, '_fit_ellipse_annulus', lambda _: geometry)
    monkeypatch.setattr(ellipse, '_fit_ellipse_annulus', lambda _: geometry)
    preview = tmp_path / 'preview.png'
    unwrap.save_ring_text_band_preview(path, preview)
    image = cv2.imread(str(preview))
    assert np.all(image[glyphs > 0] == source[glyphs > 0])
    actual = tmp_path / 'actual.png'
    unwrap.save_unwrapped_seal(path, actual, None, elliptical=True,
                               normalized=True, single_line=True)
    # Explicitly supplying the same complete maps must produce identical OCR pixels.
    from receipt_ocr.imaging.ring_border import RingGuide
    maps = ellipse._ellipse_normal_maps(geometry[0], geometry[1], True)
    guide = RingGuide(geometry, mask.shape, np.zeros((1, 2)), np.ones(1, bool), *maps)
    expected = tmp_path / 'expected.png'
    unwrap.save_unwrapped_seal(path, expected, None, elliptical=True,
                               normalized=True, single_line=True, ring_guide=guide)
    assert actual.read_bytes() == expected.read_bytes()
