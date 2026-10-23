"""A separate department row must remain observed evidence in red and blue seals."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.layout_fields import inner_role
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone
from receipt_ocr.recognition.seal.preprocess import inputs
from receipt_ocr.recognition.seal.preprocess.detected_layout import detected_rows
from receipt_ocr.recognition.seal.preprocess.multi_zone import prepare_multi_zone
from receipt_ocr.imaging.contracts import SealRegion


COMPANY = '测试科技有限公司'
DEPARTMENT = '仓储部'
REQUIRED = COMPANY + DEPARTMENT + '收货章'


def boxes():
    return [
        {'text': DEPARTMENT, 'confidence': .96,
         'points': [[405, 345], [585, 345], [585, 410], [405, 410]]},
        {'text': '收货章', 'confidence': .99,
         'points': [[345, 475], [655, 475], [655, 550], [345, 550]]},
    ]


def stamp(tmp_path, color):
    image = np.full((800, 1000, 3), 255, np.uint8)
    ink = (80, 80, 245) if color == 'red' else (245, 80, 80)
    cv2.ellipse(image, (500, 400), (440, 330), 0, 0, 360, ink, 6)
    for left in (410, 470, 530):
        image[350:405, left:left+40] = ink
    for left in (350, 450, 550):
        image[480:545, left:left+90] = ink
    cv2.putText(image, 'COMPANY', (300, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.4, ink, 4)
    source = tmp_path / 'stamp.png'
    cv2.imwrite(str(source), image)

    def detect(path, **kwargs):
        if 'station-search' in path.name:
            return []
        h, w = cv2.imread(str(path)).shape[:2]
        return [{**box, 'points': (np.asarray(box['points']) * [w/1000, h/800]).tolist()}
                for box in boxes()]

    return source, image, SimpleNamespace(detect_boxes=detect)


@pytest.mark.parametrize('text', ['仓储部', '财务部', '物流管理部'])
def test_department_role_comes_from_observed_text(text):
    assert inner_role(text) == 'department'
    assert inner_role(text + '收货章') == 'type'
    assert not inner_role('测试科技有限公司')


def test_unknown_or_overlapping_interior_rows_are_not_hidden():
    image = np.full((800, 1000, 3), 255, np.uint8)
    rho = np.zeros(image.shape[:2])
    assert [r['role'] for r in detected_rows(boxes(), image, rho)] == ['department', 'type']
    invalid = boxes()
    invalid[0]['text'] = '未确认内容'
    assert detected_rows(invalid, image, rho) == []
    invalid = boxes()
    invalid[0]['points'] = invalid[1]['points']
    assert detected_rows(invalid, image, rho) == []


@pytest.mark.parametrize('unknown_extra', [False, True])
def test_complementary_scales_keep_whole_type_row_and_native_department(tmp_path, unknown_extra):
    from receipt_ocr.recognition.seal.preprocess.detected_layout import _detect_layout_rows
    image = np.full((800, 1000, 3), 255, np.uint8)
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), image)
    department, purpose = boxes()

    def detect(path, **kwargs):
        h, w = cv2.imread(str(path)).shape[:2]
        if w == 512:
            return [{**purpose, 'points': (np.asarray(purpose['points']) * [w/1000, h/800]).tolist()}]
        native = [department, *[
            {'text': text, 'confidence': .99,
             'points': [[left, 480], [left+85, 480], [left+85, 545], [left, 545]]}
            for text, left in [('收', 350), ('货', 450), ('章', 550)]
        ]]
        if unknown_extra:
            native.append({'text': '未确认内容', 'confidence': .99,
                           'points': [[350, 200], [650, 200], [650, 250], [350, 250]]})
        return native

    _, rows = _detect_layout_rows(
        source, tmp_path / 'out', 0, image, np.zeros(image.shape[:2]),
        reader=SimpleNamespace(detect_boxes=detect), provider='paddle_v6')
    if unknown_extra:
        assert rows == []
    else:
        assert [r['detected_text'] for r in rows] == [DEPARTMENT, '收货章']
        np.testing.assert_allclose(rows[1]['polygon'], purpose['points'])


def test_verified_pre_enhancement_geometry_survives_broken_enhanced_border(tmp_path, monkeypatch):
    from receipt_ocr.recognition.seal.preprocess import multi_zone
    from receipt_ocr.recognition.seal.preprocess.detected_layout import fitted_guide
    source, image, reader = stamp(tmp_path, 'blue')
    guide = fitted_guide(image, color='blue')
    assert guide is not None
    # The new image lost its rim, but the earlier geometry still describes
    # this exact pixel frame. A fresh border fit cannot recover that evidence.
    cv2.ellipse(image, (500, 400), (440, 330), 0, 0, 360, (255, 255, 255), 16)
    cv2.imwrite(str(source), image)
    assert fitted_guide(image, color='blue') is None
    monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **k: None)
    plan = prepare_multi_zone(
        source, tmp_path / 'out', 0, reader=reader, provider='paddle_v6',
        color='blue', ring_guide=guide)
    assert plan is not None
    assert plan['method'] == 'ellipse_department_rows'
    assert plan['expected_inner_roles'] == ['department', 'type']


@pytest.mark.parametrize('color', ['red', 'blue'])
@pytest.mark.parametrize('fitted_fallback', [False, True])
def test_department_and_type_rows_are_separate_inputs_and_preserve_ring_pixels(tmp_path, monkeypatch, color, fitted_fallback):
    source, image, reader = stamp(tmp_path, color)
    if fitted_fallback:
        from receipt_ocr.recognition.seal.preprocess import multi_zone
        monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **k: None)
    before = source.read_bytes()
    plan = prepare_multi_zone(source, tmp_path / 'out', 0, reader=reader,
                              provider='paddle_v6', color=color)
    assert plan is not None
    assert plan['method'] == 'ellipse_department_rows'
    assert plan['expected_inner_roles'] == ['department', 'type']
    assert [job[2] for job in plan['jobs']] == ['layout_inner', 'layout_inner', 'ring']
    mask = cv2.imread(str(plan['mask']), 0)
    assert mask[370, 425] and mask[500, 380]
    assert not mask[70:120].any()
    masked = cv2.imread(str(plan['ring_input']))
    assert np.array_equal(masked[mask == 0], image[mask == 0])
    assert np.all(masked[mask > 0] == 255)
    ring = cv2.imread(str(plan['jobs'][-1][1]), 0)
    assert (ring < 180).sum() > 500
    assert source.read_bytes() == before


@pytest.mark.parametrize('shape', ['round', 'ellipse'])
def test_blue_pipeline_actually_dispatches_multiple_interior_rows(tmp_path, monkeypatch, shape):
    source, image, reader = stamp(tmp_path, 'blue')
    monkeypatch.setattr(inputs, 'OcrReader', lambda: reader)
    monkeypatch.setattr(inputs, 'classify_shape', lambda *args: shape)
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, 'blue', '收货客户章', 0), 0,
        tmp_path / 'out', '/files', 'paddle_v6', 'none',
    )
    assert artifact['errors'] == []
    assert not artifact.get('multi_zone_fallback')
    assert artifact['shape_code'] == shape
    assert artifact['multi_zone_layout']['method'] == 'ellipse_department_rows'
    assert [item['channel'] for item, _ in jobs] == ['layout_inner', 'layout_inner', 'ring']
    assert all(item['ocr_input'] for item, _ in jobs)


def artifact():
    return {'multi_zone_layout': {
        'method': 'ellipse_department_rows', 'expected_inner_roles': ['department', 'type'],
        'rows': [{'role': 'department', 'detected_text': DEPARTMENT, 'detected_confidence': .96}],
    }, 'reads': [
        {'channel': channel, 'text': text, 'confidence': confidence, 'image_url': f'/{i}.png'}
        for i, (channel, text, confidence) in enumerate([
            ('layout_inner', DEPARTMENT, .94), ('layout_inner', '收货章', .99),
            ('ring', COMPANY, .98),
        ])
    ]}


def test_only_agreeing_department_observations_join_the_complete_stamp():
    a = artifact()
    texts = finalize_multi_zone(a)
    assert texts == [REQUIRED]
    assert a['combined_text'] == REQUIRED
    assert a['department_text'] == DEPARTMENT
    assert a['reads'][0]['zone_label'] == '章内部门'
    assert a['multi_zone_layout']['status'] == 'recognized'
    assert compare_seal_text_strict(REQUIRED, texts)['status'] == '匹配'
    assert compare_seal_text_strict(REQUIRED.replace(DEPARTMENT, '财务部'), texts)['status'] != '匹配'


@pytest.mark.parametrize('case', ['missing', 'weak', 'duplicate', 'wrong_role', 'disagreement', 'weak_detection'])
def test_uncertain_or_conflicting_department_evidence_does_not_create_a_match(case):
    a = artifact()
    if case == 'missing':
        a['reads'].pop(0)
    elif case == 'weak':
        a['reads'][0]['confidence'] = .5
    elif case == 'duplicate':
        a['reads'].insert(0, deepcopy(a['reads'][0]))
    elif case == 'wrong_role':
        a['reads'][0]['text'] = '另一家公司'
    elif case == 'disagreement':
        a['reads'][0]['text'] = '全储部'
        a['reads'][0]['confidence'] = .99
    else:
        a['multi_zone_layout']['rows'][0]['detected_confidence'] = .4
    assert finalize_multi_zone(a) == []
    assert not a['combined_text']
    assert a['multi_zone_layout']['status'] == 'needs_review'
