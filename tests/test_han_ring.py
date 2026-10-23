from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal.preprocess import han_ring, inputs
from receipt_ocr.recognition.seal import pipeline


def mixed_strip():
    strip = np.full((240, 3200), 255, np.uint8)
    for x in range(600, 1400, 110):
        cv2.rectangle(strip, (x, 30), (x+80, 179), 40, 4)
    # Short detached components at both ends belong to the edge characters.
    for x in [500, 1480]:
        strip[30:95, x:x+70] = 40
        strip[110:175, x:x+70] = 40
    for x in [100, 2450]:
        strip[50:145, x:x+150] = 40
    for x in range(1750, 2250, 40):
        strip[25:65, x:x+25] = 40
    return strip


@pytest.mark.parametrize('scale', [.7, 1, 1.5])
def test_edge_glyph_components_are_retained_but_digits_and_other_run_excluded(scale):
    strip = cv2.resize(mixed_strip(), None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    result = han_ring.han_arc_bounds(strip)
    assert result is not None
    left, top, right, bottom = result['box']
    assert left < 500*scale and right >= 1550*scale
    assert right < 1750*scale and left > 250*scale
    assert top < 30*scale and bottom >= 179*scale


@pytest.mark.parametrize('kind', ['digits_only', 'ordinary_company', 'two_tall_runs', 'white'])
def test_ambiguous_and_non_mixed_layouts_keep_existing_route(kind):
    strip = mixed_strip()
    if kind == 'digits_only':
        strip[:, :1600] = 255
    elif kind == 'ordinary_company':
        strip[:, :400] = 255
        strip[:, 2400:] = 255
    elif kind == 'two_tall_runs':
        for x in range(2000, 2850, 110):
            cv2.rectangle(strip, (x, 30), (x+80, 179), 40, 4)
    else:
        strip[:] = 255
    assert han_ring.han_arc_bounds(strip) is None


def test_nearby_short_stems_and_dots_do_not_expand_han_boundary():
    strip = mixed_strip()
    strip[30:65, 478:486] = 40
    strip[75:150, 470:490] = 40
    # The true edge glyph at x=500 has two parts spanning the full line height.
    result = han_ring.han_arc_bounds(strip)
    assert result is not None
    assert 490 < result['box'][0] < 500


def prepare_source(tmp_path, monkeypatch):
    strip = mixed_strip()
    image = np.stack([strip, strip, np.full_like(strip, 255)], axis=2)
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    geometry = ((1000., .01, ((500., 500.), (1000., 1000.), 0.)), None, True)
    monkeypatch.setattr(han_ring, '_fit_ellipse_annulus', lambda *a: geometry)
    yy, xx = np.indices(strip.shape, dtype=np.float32)
    monkeypatch.setattr(han_ring, '_ellipse_proportional_maps', lambda *a: (xx, yy))
    return source


@pytest.mark.parametrize('failure', ['', 'weak', 'nan', 'different', 'non_han', 'partial', 'empty'])
def test_only_agreeing_high_confidence_han_company_reads_are_promoted(tmp_path, monkeypatch, failure):
    source = prepare_source(tmp_path, monkeypatch)
    original = source.read_bytes()
    calls = []
    def read(path, *, provider):
        calls.append(path)
        value, score = '某某测试电子有限公司', .99
        if failure == 'empty':
            return []
        if failure == 'weak':
            score = .90
        elif failure == 'nan':
            score = float('nan')
        elif failure == 'different' and len(calls) > 1:
            value = '另一个测试电子有限公司'
        elif failure == 'non_han':
            value += '123'
        elif failure == 'partial':
            value = '某某测试电子有限公'
        return [TextObservation(value, score, 0, 0, 1, 1)]
    result = han_ring.prepare_han_ring(source, tmp_path/'out', 0,
                                     reader=SimpleNamespace(read_line=read), provider='paddle_v6')
    assert (result is not None) == (not failure)
    assert source.read_bytes() == original
    assert all('full' not in p.name for p in calls)
    if result:
        assert len(calls) == 2
        assert result['evidence']['reads'][0]['text'] == '某某测试电子有限公司'
        assert result['full'].is_file() and result['band'].is_file()


def test_pipeline_ocr_and_preview_use_selected_han_image(monkeypatch, tmp_path):
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), np.full((200, 200, 3), 255, np.uint8))
    selected, full, band = [tmp_path/name for name in ['selected.png', 'full.png', 'band.png']]
    for p in [selected, full, band]:
        cv2.imwrite(str(p), np.full((100, 400, 3), 255, np.uint8))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *a: 'round')
    monkeypatch.setattr(inputs, 'prepare_han_ring', lambda *a, **k: {
        'ring': selected, 'full': full, 'band': band, 'evidence': {'method': 'verified_han_company_arc'}})
    monkeypatch.setattr(inputs, 'save_unwrapped_seal',
                        lambda source, dest, *a, **k: cv2.imwrite(str(dest), np.full((100, 500, 3), 255, np.uint8)))
    def unexpected(*a, **k):
        raise AssertionError('legacy company selection must not overwrite verified Han input')
    monkeypatch.setattr(inputs, 'save_company_ring_input', unexpected)
    seen = []
    class Reader:
        def read_line(self, path, *, provider):
            seen.append(path)
            return [TextObservation('某某测试电子有限公司', .99, 0, 0, 1, 1)]
    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    texts, artifacts = pipeline._recognize_local_seals(
        source, [], [SealRegion(0, 0, 1, 1, 'red', '客户章', 0)],
        tmp_path/'artifacts', '/files/test', 'paddle_v6', orientation_mode='none')
    artifact = artifacts[0]
    assert not artifact['errors']
    assert seen == [selected]
    assert artifact['unwrapped_url'].endswith('/selected.png')
    assert artifact['reads'][0]['image_url'] == artifact['unwrapped_url']
    assert artifact['unwrapped_full_url'].endswith('/full.png')
    full_input = next(i for i in artifact['inputs'] if i['name'] == 'ring-full')
    assert not full_input['ocr_input']
    assert texts == ['某某测试电子有限公司']
