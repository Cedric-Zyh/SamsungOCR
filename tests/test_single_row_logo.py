"""Star artwork and one rim glyph must not create false internal text rows."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import multi_zone, detected_layout, station_layout
from receipt_ocr.recognition.seal.preprocess.single_row_logo import single_row_logo_evidence


def sample(scale=1, tips=5):
    image = np.full((800, 1000, 3), 255, np.uint8)
    points = []
    for i in range(2*tips):
        angle = -np.pi/2 + i*np.pi/tips
        radius = 80 if i % 2 == 0 else 33
        points.append([500+radius*np.cos(angle), 360+radius*np.sin(angle)])
    cv2.fillPoly(image, [np.rint(points).astype(np.int32)], (70, 70, 255))
    for x in range(275, 725, 90):
        cv2.rectangle(image, (x, 535), (x+45, 615), (70, 70, 255), -1)
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx-500*scale)/(450*scale), (yy-400*scale)/(350*scale))
    rows = [{'box': [550, 150, 610, 210]}, {'box': [300, 270, 700, 440]},
            {'box': [275, 535, 725, 615]}]
    rows = [{'box': [round(v*scale) for v in r['box']]} for r in rows]
    detected = {'rows': [{'role': 'type', 'detected_text': '收货专用章',
                         'detected_confidence': .99,
                         'box': [v*scale for v in [250, 525, 750, 625]]}]}
    orientation = {'type_band_text': '收货专用章', 'type_band_confidence': .99}
    return image, rho, rows, detected, orientation


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_verified_single_type_and_star_reject_false_component_rows(scale):
    image, rho, rows, detected, orientation = sample(scale)
    before = image.copy()
    evidence = single_row_logo_evidence(image, rho, rows, detected, orientation)
    assert evidence['selected'] == 'single_type_row'
    assert evidence['type_text'] == '收货专用章'
    assert evidence['rejected_component_roles'] == ['rim_fragment', 'star_artwork']
    np.testing.assert_array_equal(image, before)


@pytest.mark.parametrize('case', ['no_orientation', 'weak_orientation', 'weak_detection', 'different_text',
                                  'unknown_row', 'department_row', 'phone_row', 'station_row',
                                  'extra_components', 'interior_fragment', 'no_star', 'six_tips'])
def test_uncertain_or_real_extra_rows_keep_multi_row_route(case):
    image, rho, rows, detected, orientation = sample(tips=6 if case == 'six_tips' else 5)
    if case == 'no_orientation':
        orientation = None
    elif case == 'weak_orientation':
        orientation['type_band_confidence'] = .94
    elif case == 'weak_detection':
        detected['rows'][0]['detected_confidence'] = .94
    elif case == 'different_text':
        orientation['type_band_text'] = '业务专用章'
    elif case == 'unknown_row':
        detected['rows'] = []
    elif case in ('department_row', 'phone_row', 'station_row'):
        detected['rows'].insert(0, {'role': case.removesuffix('_row')})
    elif case == 'extra_components':
        rows.insert(1, {'box': [350, 230, 650, 265]})
    elif case == 'interior_fragment':
        rho[150:210, 550:610] = .2
    elif case == 'no_star':
        image[250:450, 400:600] = 255
        cv2.rectangle(image, (430, 290), (570, 430), (70, 70, 255), -1)
    assert single_row_logo_evidence(image, rho, rows, detected, orientation) is None


def test_false_component_fallback_is_skipped_and_audited(tmp_path, monkeypatch):
    image, rho, rows, detected, orientation = sample()
    source = tmp_path / 'stamp.png'
    cv2.imwrite(str(source), image)
    guide = SimpleNamespace(annulus=[(0, 0, ((500, 400), (900, 700), 0))])
    monkeypatch.setattr(station_layout, 'prepare_station_layout', lambda *a, **kw: None)
    monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **kw: guide)
    monkeypatch.setattr(multi_zone, '_inner_rows', lambda *a: rows)

    def detect(*args, evidence=None, **kwargs):
        evidence.update(deepcopy(detected))
        return None

    monkeypatch.setattr(detected_layout, 'prepare_detected_layout', detect)
    monkeypatch.setattr(multi_zone, '_arc_maps', lambda *a, **kw: pytest.fail('false multi-row fallback'))
    audit = {}
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0, reader=object(),
                                         orientation=orientation, layout_evidence=audit) is None
    assert audit['method'] == 'verified_single_type_and_star'
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('method', ['station', 'department'])
def test_verified_real_multi_row_plan_has_priority(tmp_path, monkeypatch, method):
    image, _, _, detected, orientation = sample()
    source = tmp_path/'stamp.png'
    cv2.imwrite(str(source), image)
    plan = {'method': 'real_'+method}
    monkeypatch.setattr(station_layout, 'prepare_station_layout',
                        lambda *a, **kw: plan if method == 'station' else None)
    guide = SimpleNamespace(annulus=[(0, 0, ((500, 400), (900, 700), 0))])
    monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **kw: guide)
    monkeypatch.setattr(detected_layout, 'prepare_detected_layout', lambda *a, **kw: plan)
    monkeypatch.setattr(multi_zone, '_inner_rows', lambda *a: [])
    audit = {}
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0, reader=object(),
                                         orientation=orientation, layout_evidence=audit) is plan
    assert not audit
