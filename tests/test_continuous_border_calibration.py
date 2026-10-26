from types import SimpleNamespace

import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import continuous_company as company
from test_continuous_company_arc import arc_image


@pytest.mark.parametrize('color', ['red', 'blue'])
@pytest.mark.parametrize('ratio', [.65, 1])
def test_visible_border_corrects_oversized_estimate_without_changing_source(color, ratio):
    image, _, actual = arc_image(range(140, 401, 15), ratio=ratio, color=color)
    center = np.asarray(actual.annulus[0][2][0])
    points = center + (actual.points - center) / .88
    guide = SimpleNamespace(annulus=actual.annulus, points=points,
                            summary=lambda: {'method': 'ellipse_estimate'})
    source_before = image.copy()
    calibrated = company.calibrate_estimated_border(image, guide, color=color)
    assert calibrated is not guide
    assert calibrated.summary()['scale'] == pytest.approx(.88, abs=.006)
    np.testing.assert_allclose(calibrated.points, actual.points, atol=3)
    np.testing.assert_array_equal(image, source_before)
    np.testing.assert_array_equal(guide.points, points)


def test_measured_border_does_not_get_recalibrated(monkeypatch):
    guide = SimpleNamespace(summary=lambda: {'method': 'observed_ellipse_border'})
    monkeypatch.setattr(company, '_forward_strip', lambda *a, **kw: pytest.fail('already measured'))
    assert company.calibrate_estimated_border(None, guide) is guide


@pytest.mark.parametrize('kind', ['blank', 'local_ink', 'dense_pattern', 'correct_radius'])
def test_missing_or_ambiguous_border_cannot_relocate_company_letters(monkeypatch, kind):
    strip = np.full((200, 1000), 255, np.uint8)
    peak = 112 if kind != 'correct_radius' else 60
    if kind == 'local_ink':
        strip[peak, :490] = 0  # two sides alone do not establish a full ring
    elif kind == 'dense_pattern':
        strip[peak-25:peak+26] = 0
    elif kind == 'correct_radius':
        strip[peak] = 0
    guide = SimpleNamespace(summary=lambda: {'method': 'ellipse_estimate'})
    monkeypatch.setattr(company, '_forward_strip', lambda *a, **kw: strip)
    assert company.calibrate_estimated_border(None, guide) is guide


def test_calibrated_pixels_are_the_ones_sent_to_ocr(tmp_path, monkeypatch):
    from test_continuous_company_recovery import evidence, setup_image
    from receipt_ocr.domain.ocr import TextObservation
    setup_image(tmp_path, monkeypatch)
    a = evidence()
    calls = []
    sentinel = object()
    def calibrate(image, guide, **kwargs):
        calls.append('calibrate')
        return sentinel
    def sample(image, guide, **kwargs):
        assert guide is sentinel
        calls.append('sample')
        return np.full((30, 100), 210, np.uint8), {
            'degrees': [130, 410], 'radial_range': [.6, .94],
            'border': {'method': 'radial_border_refit'},
        }
    def read(path, **kwargs):
        import cv2
        assert np.all(cv2.imread(str(path), 0) == 210)
        calls.append('read')
        return [TextObservation('实际读数', .99, 0, 0, 1, 1)]
    monkeypatch.setattr(company, 'calibrate_estimated_border', calibrate)
    monkeypatch.setattr(company, 'continuous_company_pixels', sample)
    assert company.read_continuous_company(a, tmp_path, 0, '/test',
                                          reader=SimpleNamespace(read_line=read), provider='paddle_v6')
    assert calls == ['calibrate', 'sample', 'read']
    assert a['unwrapped_text'] == '实际读数'
    assert a['ring_border_detection']['method'] == 'radial_border_refit'
