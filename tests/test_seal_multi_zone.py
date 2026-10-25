from copy import deepcopy

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.recognition.seal.preprocess.multi_zone import _inner_rows, prepare_multi_zone
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone


def test_inner_rows_leave_same_height_rim_components_outside():
    image = np.full((600, 800, 3), 255, np.uint8)
    y, x = np.indices(image.shape[:2])
    rho = np.hypot((x-400)/370, (y-300)/270)
    for top, lefts in [(220, [370, 398, 426]), (290, [270, 325, 380, 435, 490]),
                       (370, [300, 345, 390, 435, 480])]:
        for left in lefts:
            cv2.rectangle(image, (left, top), (left+20, top+35), (80, 80, 255), -1)
    # Outer arc glyph fragments share the middle row's height.
    for left in (90, 115, 140, 675):
        cv2.rectangle(image, (left, 290), (left+12, 325), (80, 80, 255), -1)
    rows = _inner_rows(image, rho, (740, 540))
    assert len(rows) == 3
    assert rows[1]['box'] == [270, 290, 511, 326]


def test_empty_or_simple_layout_falls_back_without_writing(tmp_path):
    source = tmp_path/'simple.png'
    image = np.full((400, 600, 3), 255, np.uint8)
    cv2.ellipse(image, (300, 200), (260, 160), 0, 0, 360, (60, 60, 255), 5)
    for left in range(200, 400, 40):
        cv2.rectangle(image, (left, 210), (left+20, 250), (60, 60, 255), -1)
    cv2.imwrite(str(source), image)
    output = tmp_path/'result'
    assert prepare_multi_zone(source, output, 0) is None
    assert not output.exists()


def artifact():
    pairs = [('ring', '三星电子服务中心'), ('layout_inner', '取机专用章'),
             ('layout_inner', '(1)'), ('layout_inner', '5785258站'),
             ('layout_lower', '电话：020-31103112')]
    return {'multi_zone_layout': {}, 'original_url': '/original.png', 'color_isolated_url': '/clean.png',
            'shape_code': 'ellipse', 'color': 'red', 'reads': [
                {'channel': ch, 'text': text, 'confidence': .99, 'image_url': f'/{i}.png'}
                for i, (ch, text) in enumerate(pairs)]}


def sample():
    a = artifact()
    a['multi_zone_layout'] = {'method': 'ellipse_components'}
    return a


def test_actual_regions_combine_without_importing_phone_into_name():
    a = sample()
    result = finalize_multi_zone(a)
    assert result[0] == '三星电子服务中心取机专用章5785258站'
    assert result[1] == '三星电子服务中心取机专用章(1)5785258站'
    assert a['stamp_number_text'] == '(1)'
    assert a['lower_arc_text'] == '电话：020-31103112'
    a['unwrapped_text'] = '三星电子服务中心'
    candidate = seal_candidates({'fields': {'签章要求': result[0]},
                                'processing_artifacts': {'seals': [a]}})[0]
    assert candidate['text_status'] == '匹配'
    assert all('020' not in text for text in candidate['texts'])


@pytest.mark.parametrize('case', ['weak_station', 'missing_station', 'ambiguous_station', 'failed_ring'])
def test_incomplete_or_ambiguous_ocr_does_not_create_matching_candidate(case):
    a = sample()
    if case == 'weak_station':
        a['reads'][3]['confidence'] = .4
    elif case == 'missing_station':
        a['reads'].pop(3)
    elif case == 'ambiguous_station':
        a['reads'].append(deepcopy(a['reads'][3]))
    else:
        a['reads'].pop(0)
    assert finalize_multi_zone(a) == []
    assert a['multi_zone_layout']['status'] == 'needs_review'


def test_unrecognized_lower_arc_remains_visible_without_becoming_a_company_candidate():
    a = sample()
    a['reads'][-1]['text'] = '某段下弧文字'
    finalize_multi_zone(a)
    assert a['reads'][-1]['zone_label'] == '下弧文字'
    assert a['reads'][-1]['matching'] is False
    assert '某段' not in a['combined_text']


def contact_artifact(*, lower=False):
    pairs = [('ring', '测试科技有限公司'), ('layout_inner', '维修专用章')]
    pairs += [('layout_inner', '(3)'), ('layout_lower', 'Tel:0451-53608531')] if lower else [
        ('layout_inner', '0451-53608531')]
    return {'multi_zone_layout': {'method': 'ellipse_detected_rows',
            'expected_inner_roles': ['type', 'number' if lower else 'phone'],
            'arcs': {'upper': {}, **({'lower': {}} if lower else {})}},
            'reads': [{'channel': ch, 'text': text, 'confidence': .99, 'image_url': f'/{i}.png'}
                      for i, (ch, text) in enumerate(pairs)]}


@pytest.mark.parametrize('lower', [False, True])
def test_contact_layout_keeps_phone_separate_and_combines_observed_fields(lower):
    a = contact_artifact(lower=lower)
    result = finalize_multi_zone(a)
    assert '测试科技有限公司维修专用章' in result
    assert '测试科技有限公司维修专用章电话:0451-53608531' in result
    assert '测试科技有限公司维修专用章045153608531' in result
    assert a['phone_text'] == '0451-53608531'
    assert a['multi_zone_layout']['status'] == 'recognized'
    assert all(r['matching'] is False for r in a['reads'])
    if lower:
        assert '维修专用章(3)电话:' in a['combined_text']
        assert a['reads'][-1]['zone_label'] == '下弧电话'
    else:
        assert a['reads'][-1]['zone_label'] == '章内电话'


@pytest.mark.parametrize('case', ['missing', 'low_confidence', 'unknown', 'duplicate', 'different_role'])
def test_contact_layout_does_not_hide_incomplete_or_conflicting_rows(case):
    a = contact_artifact(lower=True)
    if case == 'missing':
        a['reads'].pop()
    elif case == 'low_confidence':
        a['reads'][-1]['confidence'] = .5
    elif case == 'unknown':
        a['reads'][-1]['text'] = '另一段公司名称'
    elif case == 'duplicate':
        a['reads'].append(deepcopy(a['reads'][-1]))
    else:
        a['reads'][2]['text'] = '12345站'
    assert finalize_multi_zone(a) == []
    assert a['multi_zone_layout']['status'] == 'needs_review'


def test_contact_label_normalization_does_not_invent_phone_digits():
    from receipt_ocr.recognition.seal.layout_fields import phone_value
    assert phone_value('Te1:0871-65309968') == '0871-65309968'
    assert phone_value('电话：0451－53608531') == '0451-53608531'
    assert phone_value('Tel:O451-53608531') == ''
    assert phone_value('0451-53608531其他字') == ''


def test_complete_detected_rows_are_not_clipped_to_the_central_radius():
    from receipt_ocr.recognition.seal.preprocess.detected_layout import detected_rows
    image = np.full((907, 1098, 3), 255, np.uint8)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx-549)/500, (yy-453)/375)
    boxes = [
        {'text': '0451-53608531', 'confidence': .99,
         'points': [[258, 456], [880, 466], [878, 575], [256, 565]]},
        {'text': '维修专用章', 'confidence': .99,
         'points': [[284, 574], [862, 576], [861, 733], [283, 731]]},
        {'text': '公司', 'confidence': .99,
         'points': [[910, 486], [1004, 486], [1004, 557], [910, 557]]},
    ]
    rows = detected_rows(boxes, image, rho)
    assert len(rows) == 2
    assert rows[0]['polygon'] == boxes[0]['points']
    assert rows[1]['polygon'] == boxes[1]['points']
    assert detected_rows(boxes[:1], image, rho) == []
    overlapping = deepcopy(boxes[:2])
    overlapping[1]['points'] = overlapping[0]['points']
    assert detected_rows(overlapping, image, rho) == []


def test_untrusted_or_unknown_detected_row_does_not_enable_contact_layout():
    from receipt_ocr.recognition.seal.preprocess.detected_layout import detected_rows
    image = np.full((500, 700, 3), 255, np.uint8)
    rho = np.zeros(image.shape[:2])
    boxes = [{'text': '维修专用章', 'confidence': .99,
              'points': [[200, 250], [500, 250], [500, 300], [200, 300]]},
             {'text': '0451-53608531', 'confidence': .4,
              'points': [[200, 150], [500, 150], [500, 200], [200, 200]]}]
    assert detected_rows(boxes, image, rho) == []
    boxes[1].update(confidence=.99, text='不明内部内容')
    assert detected_rows(boxes, image, rho) == []


def test_contact_requirement_phone_must_match_observed_digits():
    from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
    a = contact_artifact()
    texts = finalize_multi_zone(a)
    expected = '测试科技有限公司维修专用章045153608531'
    assert compare_seal_text_strict(expected, texts)['status'] == '匹配'
    assert compare_seal_text_strict(expected[:-1]+'2', texts)['status'] != '匹配'


@pytest.mark.parametrize('detected_number', ['(3)', 'F(3)'])
def test_missing_short_number_uses_existing_tight_locator(tmp_path, monkeypatch, detected_number):
    from types import SimpleNamespace
    from receipt_ocr.recognition.seal.preprocess import multi_zone, number
    from receipt_ocr.recognition.seal.preprocess.detected_layout import prepare_detected_layout
    image = np.full((500, 700, 3), 255, np.uint8)
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    boxes = [{'text': '检测专用章', 'confidence': .99,
              'points': [[200, 200], [500, 200], [500, 250], [200, 250]]}]
    reader = SimpleNamespace(detect_boxes=lambda *args, **kwargs: boxes)
    evidence = {'status': 'detected', 'text': detected_number, 'confidence': .99,
                'box': [300, 270, 370, 320], 'method': 'tight_ink_components'}
    monkeypatch.setattr(number, 'prepare_number_input', lambda *args, **kwargs: evidence)
    mapping = {'x': np.zeros((30, 80), np.float32), 'y': np.zeros((30, 80), np.float32),
               'degrees': [180, 360], 'radial_range': [.5, .9]}
    monkeypatch.setattr(multi_zone, '_arc_maps', lambda *args, **kwargs: {
        'upper': mapping, 'lower': mapping})
    guide = SimpleNamespace(summary=lambda: {'method': 'test'})
    plan = prepare_detected_layout(source, tmp_path/'out', 0, image, guide,
                                   np.zeros(image.shape[:2]), reader=reader, provider='paddle_v6')
    assert plan['expected_inner_roles'] == ['type', 'number']
    assert plan['rows'][1]['detection_method'] == 'tight_ink_components'
    assert plan['rows'][1]['polygon'] == [[300, 270], [370, 270], [370, 320], [300, 320]]
    evidence['text'] = '3O'
    assert prepare_detected_layout(source, tmp_path/'reject', 0, image, guide,
                                   np.zeros(image.shape[:2]), reader=reader, provider='paddle_v6') is None
