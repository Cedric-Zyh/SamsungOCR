"""Keep tall side glyphs intact when unwrapping a tilted oval."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging import ellipse, unwrap


@pytest.mark.parametrize('angle', [0, 27, 90, 156])
def test_proportional_band_preserves_side_strokes_without_center_or_folds(angle):
    center = np.array([500., 500.])
    a, b = 390., 190.
    t = np.deg2rad(angle)
    rotation = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    outer = (a + b, 0., (tuple(center), (2*a, 2*b), angle))
    inner = ((a+b)*.55, 0., (tuple(center), (a*1.1, b*1.1), angle))
    source = np.full((1000, 1000), 255, np.uint8)
    # Each side has three detached strokes extending far toward the centre.
    # The old curvature-limited band loses the innermost stroke.
    for sign in (-1, 1):
        for fraction, value in ((.48, 60), (.67, 100), (.85, 140)):
            start = np.rint(center + rotation @ [sign*a*fraction, -12]).astype(int)
            end = np.rint(center + rotation @ [sign*a*fraction, 12]).astype(int)
            cv2.line(source, tuple(start), tuple(end), value, 7)
    cv2.circle(source, tuple(center.astype(int)), 30, 0, -1)
    x, y = ellipse._ellipse_proportional_maps(outer, inner, True)
    sampled = cv2.remap(source, x, y, cv2.INTER_NEAREST,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    # The bottom-ray seam can bisect a side stroke on a 90-degree oval.
    # Join the periodic ends at an empty column before counting glyphs.
    seam = np.flatnonzero(np.all(sampled == 255, axis=0))[0]
    sampled = np.roll(sampled, -int(seam), axis=1)
    for value in (60, 100, 140):
        count, _, stats, _ = cv2.connectedComponentsWithStats((sampled == value).astype('uint8'))
        assert sum(stats[1:, cv2.CC_STAT_AREA] > 5) == 2
    assert not np.any(sampled == 0)


def test_ellipse_route_and_band_preview_share_proportional_sampler(monkeypatch, tmp_path):
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), np.full((100, 100, 3), (80, 80, 240), np.uint8))
    calls = []
    def save(*args, **kwargs):
        calls.append(kwargs['proportional'])
        return True
    monkeypatch.setattr(unwrap, '_save_ellipse_annulus_unwrapped', save)
    for direct in (False, True):
        unwrap.save_unwrapped_seal(source, tmp_path/'out.png', None, elliptical=True,
                                   normalized=True, single_line=True, direct_ellipse=direct)
    assert calls == [False, True]
    monkeypatch.setattr(unwrap, '_fit_ellipse_annulus', lambda _: (None, None, True))
    seen = []
    def maps(*args):
        seen.append(True)
        return np.array([[10,90,90,10],[30,70,70,30]],np.float32), np.array([[10,10,90,90],[30,30,70,70]],np.float32)
    monkeypatch.setattr(unwrap, '_ellipse_proportional_maps', maps)
    unwrap.save_ring_text_band_preview(source, tmp_path/'preview.png', proportional=True)
    assert seen == [True]


def test_proportional_ocr_strip_has_white_margin_without_cropping_pixels(tmp_path):
    gray = np.full((300, 500), 255, np.uint8)
    mask = np.zeros_like(gray)
    cv2.ellipse(mask, (250,150),(220,110),0,0,360,255,4)
    cv2.rectangle(gray,(65,140),(140,160),70,-1)
    target = tmp_path/'ring.png'
    assert ellipse._save_ellipse_annulus_unwrapped(gray, mask, target,
                                                   single_line=True, proportional=True)
    image = cv2.imread(str(target),0)
    assert (image[:16] == 255).all() and (image[-16:] == 255).all()
    assert (image[:,:16] == 255).all() and (image[:,-16:] == 255).all()
    assert np.count_nonzero(image < 140) > 100
