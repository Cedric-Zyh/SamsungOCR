"""Adopt the tested forward sampler without changing independent lower text."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone
from receipt_ocr.recognition.seal.preprocess import continuous_company as company
from test_continuous_company_arc import arc_image


def evidence():
    pairs = [('layout_inner', '收货专用章', .99), ('layout_inner', '(1)', .99),
             ('ring', '瑞特供应链管理有限公司南京分', .97), ('layout_lower', '多', .2)]
    return {
        'region_id': 'seal-0', 'shape': '圆形', 'shape_code': 'round', 'color': 'red',
        'multi_zone_layout': {'method': 'ellipse_detected_rows',
                              'expected_inner_roles': ['type', 'number'],
                              'arcs': {'upper': {}, 'lower': {}}},
        'ring_input_source_name': 'upright.png', 'ring_input_name': 'masked.png',
        'ring_input_source_url': '/test/upright.png', 'ring_input_url': '/test/masked.png',
        'inputs': [{'id': str(i), 'channel': ch, 'status': 'completed', 'zone_name': str(i),
                    'image_url': '/test/'+str(i)+'.png'}
                   for i, (ch, _, _) in enumerate(pairs)],
        'reads': [{'id': str(i)+':0', 'input_id': str(i), 'channel': ch, 'text': text,
                   'confidence': score, 'image_url': '/test/'+str(i)+'.png'}
                  for i, (ch, text, score) in enumerate(pairs)], 'errors': [],
    }


@pytest.mark.parametrize('change', ['phone', 'unknown_lower', 'failed', 'pending', 'missing_input',
                                    'other_layout', 'phone_roles', 'existing_company'])
def test_reliable_or_unchecked_lower_text_is_not_overwritten(change):
    a = evidence()
    if change in {'phone', 'unknown_lower'}:
        a['reads'][-1].update(text='Tel:0871-65309968' if change == 'phone' else '维修服务',
                              confidence=.8)
    elif change in {'failed', 'pending'}:
        a['inputs'][-1]['status'] = change
    elif change == 'missing_input':
        a['inputs'].pop()
    elif change == 'other_layout':
        a['multi_zone_layout']['method'] = 'ellipse_station_rows'
    elif change == 'phone_roles':
        a['multi_zone_layout']['expected_inner_roles'] = ['type', 'phone']
    else:
        a['multi_zone_layout'].update(method='ellipse_continuous_name', arcs={'company': {}})
    assert not company.continuous_company_eligible(a)
    reader = SimpleNamespace(read_line=lambda *a, **kw: pytest.fail('unexpected OCR'))
    before = deepcopy(a)
    assert not company.read_continuous_company(a, '/absent', 0, '', reader=reader, provider='paddle_v6')
    assert a == before


@pytest.mark.parametrize('color', ['red', 'blue'])
@pytest.mark.parametrize('ratio', [.65, 1])
def test_forward_image_keeps_low_ends_without_stretching_the_source(color, ratio):
    image, rho, guide = arc_image(range(140, 401, 15), ratio=ratio, color=color)
    before = image.copy()
    pixels, geometry = company.continuous_company_pixels(image, guide, color=color)
    (cx, cy), _, _ = guide.annulus[0][2]
    yy, xx = np.where((image[:, :, 1] < 100) & (rho < .93))
    angles = np.rad2deg(np.mod(np.arctan2(yy-cy, xx-cx)-np.pi/2, 2*np.pi)+np.pi/2)
    assert geometry['degrees'][0] < angles.min()
    assert geometry['degrees'][1] > angles.max()
    assert geometry['radial_range'] == [.60, .94]
    assert np.count_nonzero(pixels[:, :pixels.shape[1]//8] < 100) > 20
    assert np.count_nonzero(pixels[:, -pixels.shape[1]//8:] < 100) > 20
    np.testing.assert_array_equal(image, before)


def setup_image(tmp_path, monkeypatch):
    image, _, guide = arc_image(range(140, 401, 15))
    cv2.imwrite(str(tmp_path/'upright.png'), image)
    cv2.imwrite(str(tmp_path/'masked.png'), image)
    monkeypatch.setattr(company, 'trace_ellipse_border', lambda *a, **kw: guide)
    return image


def test_only_new_same_stamp_company_read_enters_matching(tmp_path, monkeypatch):
    setup_image(tmp_path, monkeypatch)
    a = evidence()
    raw = deepcopy(a['reads'])
    name = '深圳欧瑞特供应链管理有限公司南京分公司'
    reader = SimpleNamespace(read_line=lambda *a, **kw: [TextObservation(name, .99, 0, 0, 1, 1)])
    assert company.read_continuous_company(a, tmp_path, 0, '/test', reader=reader, provider='paddle_v6')
    assert company.read_continuous_company(a, tmp_path, 0, '/test', reader=reader, provider='paddle_v6') is False
    assert [r['text'] for r in a['reads'][:4]] == [r['text'] for r in raw]
    assert [r['channel'] for r in a['reads'][2:4]] == ['split_arc_diagnostic']*2
    assert all(r['matching'] is False for r in a['reads'][2:4])
    assert a['unwrapped_text'] == name
    assert a['unwrapped_url'].endswith('/seal-0-zones-company-continuous.png')
    expected = name+'收货专用章(1)'
    assert finalize_multi_zone(a) == [expected]
    assert compare_seal_text_strict(expected.replace('(1)', '(2)'), a['matching_texts'])['status'] == '不匹配'
    assert a['continuous_company']['original_layout']['method'] == 'ellipse_detected_rows'


@pytest.mark.parametrize('mode', ['empty', 'multiple', 'error'])
def test_failed_continuous_read_preserves_the_original_artifact(tmp_path, monkeypatch, mode):
    setup_image(tmp_path, monkeypatch)
    a = evidence();before = deepcopy(a)
    def read(*args, **kwargs):
        if mode == 'error':
            raise RuntimeError('OCR unavailable')
        return [] if mode == 'empty' else [TextObservation('x', .9, 0, 0, 1, 1)]*2
    reader = SimpleNamespace(read_line=read)
    if mode == 'error':
        with pytest.raises(RuntimeError):
            company.read_continuous_company(a, tmp_path, 0, '', reader=reader, provider='paddle_v6')
    else:
        assert not company.read_continuous_company(a, tmp_path, 0, '', reader=reader, provider='paddle_v6')
    assert a == before


def test_pipeline_replaces_split_candidates_but_keeps_other_stamps(tmp_path, monkeypatch):
    directory = tmp_path/'seals';directory.mkdir()
    setup_image(directory, monkeypatch)
    a = evidence();a['reads'] = []
    old = evidence()['reads']
    jobs = []
    for item, row in zip(a['inputs'], old):
        item['detected_rows'] = [TextObservation(row['text'], row['confidence'], 0, 0, 1, 1).to_dict()]
        jobs.append((item, directory/'unused.png'))
    other = {'region_id': 'seal-1', 'shape_code': 'rectangle', 'rectangular_layout': 'single_detection',
             'rectangular_fixed_rows': [], 'reads': [], 'inputs': [], 'errors': []}
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args, **kw: (a, jobs) if args[2] == 0 else (other, []))
    name = '深圳欧瑞特供应链管理有限公司南京分公司'
    monkeypatch.setattr(pipeline, 'OcrReader', lambda: SimpleNamespace(
        read_line=lambda *a, **kw: [TextObservation(name, .99, 0, 0, 1, 1)]))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path/'source.png', [], [object(), object()], tmp_path, '/test', 'paddle_v6',
        requirement='不同的公司收货专用章(9)',
    )
    assert name in texts and name+'收货专用章(1)' in texts
    assert old[2]['text'] not in texts and '多' not in texts
    assert len(artifacts) == 2 and all(not x['errors'] for x in artifacts)
    assert artifacts[0]['unwrapped_text'] == name
