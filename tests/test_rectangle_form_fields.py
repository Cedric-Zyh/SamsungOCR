from copy import deepcopy

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.rectangle_form import mark_empty_receiving_fields


def artifact():
    return {'schema_version': 2, 'region_id': 'seal-0', 'shape_code': 'rectangle',
            'rectangular_layout': 'frame_rows', 'rectangular_fixed_rows': [{}, {}, {}, {}],
            'inputs': [], 'errors': [], 'reads': []}


@pytest.mark.parametrize('date_caption', ['收货日期：年 月 日', '日期：', '日期：年 月 日'])
def test_empty_receiving_captions_remain_evidence_but_not_part_of_name(monkeypatch, tmp_path, date_caption):
    a = artifact()
    words = ['广州花东仓020DCH', '收货专用章 ①', '收货人：', date_caption]
    jobs = []
    for i in range(4):
        item = {'id': f'row-{i}', 'channel': 'body', 'rectangle_row_index': i,
                'image_url': f'/row-{i}.png', 'read_mode': 'line'}
        jobs.append((item, tmp_path/f'{i}.png'))

    class Reader:
        def read_line(self, path, **kwargs):
            return [TextObservation(words[int(path.stem)], .99, 0, 0, 1, 1)]

    monkeypatch.setattr(pipeline, 'OcrReader', Reader)
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args, **kwargs: (a, jobs))
    _, artifacts = pipeline._recognize_local_seals(
        tmp_path/'source.png', [], [object()], tmp_path/'out', '/test', 'paddle_v6')
    result = artifacts[0]
    assert result['rectangular_text'] == '广州花东仓020DCH收货专用章 ①'
    assert len(result['reads']) == 4
    assert [r['text'] for r in result['rectangular_form_fields']] == words[2:]


@pytest.mark.parametrize('text', ['收货日期：2026年10月22日', '收货人：张三',
                                '收货日期科技有限公司', '收货专用章（1）',
                                '日期：2026年10月22日', '日期科技有限公司'])
def test_actual_values_and_business_text_are_never_treated_as_empty_captions(text):
    a = artifact()
    a['reads'] = [{'channel': 'body', 'text': '收货专用章', 'confidence': .99},
                  {'channel': 'body', 'text': text, 'confidence': .99}]
    original = deepcopy(a)
    mark_empty_receiving_fields(a)
    assert a == original


def test_captions_on_unverified_layout_are_unchanged():
    a = artifact()
    a['reads'] = [{'channel': 'body', 'text': '收货日期：年月日', 'confidence': .99}]
    original = deepcopy(a)
    mark_empty_receiving_fields(a)
    assert a == original


def receiving_form_with_values(person='收货人：张三', date='日期1'):
    a = artifact()
    a['shape'] = '矩形'
    a['original_url'] = '/files/original.png'
    a['color_isolated_oriented_url'] = '/files/clean.png'
    a['color'] = 'red'
    a['rectangular_fixed_rows'] = [
        {'crop_box': [10, 10+i*100, 410, 110+i*100], 'coordinate_space': 'oriented'}
        for i in range(4)]
    a['rectangular_frame'] = {'crop_box': [10, 10, 410, 410], 'canonical_width': 400,
        'padding': 32, 'detected_boxes': [
            {'text': caption, 'confidence': .99,
             'points': [[42, 52+i*100], [160, 52+i*100], [160, 102+i*100], [42, 102+i*100]]}
            for i, caption in [(2, '收货人'), (3, '日期')]]}
    words = ['鄂州枢纽花湖仓 027RDC', '收货专用章①', person, date]
    a['reads'] = [{'channel': 'body', 'text': text, 'confidence': .99,
                   'rectangle_row_index': i, 'image_url': f'/files/row-{i}.png'}
                  for i, text in enumerate(words)]
    return a


@pytest.mark.parametrize('person,date', [
    ('收货人', '日期1'), ('收货人：张三', '日期：2026年10月24日'),
    ('贝八', '2026-10-24'), ('张三', '日期'),
])
def test_entire_detected_receiving_and_date_cells_are_excluded_even_when_filled(monkeypatch, tmp_path, person, date):
    from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
    from receipt_ocr.domain.seal_standards import seal_candidates
    a = receiving_form_with_values(person, date)
    words = [r['text'] for r in a['reads']];a['reads'] = []
    jobs = []
    for i in range(4):
        item = {'id': f'row-{i}', 'channel': 'body', 'rectangle_row_index': i,
                'image_url': f'/files/row-{i}.png', 'read_mode': 'line'}
        jobs.append((item, tmp_path/f'{i}.png'))
    monkeypatch.setattr(pipeline, 'OcrReader', lambda: type('Reader', (), {
        'read_line': lambda self, path, **kw: [TextObservation(words[int(path.stem)], .99, 0, 0, 1, 1)]})())
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *args, **kw: (a, jobs))
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path/'source.png', [], [object()], tmp_path/'out', '/files/test', 'paddle_v6')
    result = artifacts[0]
    expected = '鄂州枢纽花湖仓027RDC收货专用章①'
    assert result['rectangular_text'] == words[0]+words[1]
    assert all(r['matching'] is False for r in result['reads'][2:])
    assert [r['text'] for r in result['reads']] == words
    assert person not in texts and date not in texts
    assert compare_seal_text_strict(expected, texts)['status'] == '匹配'
    assert compare_seal_text_strict(expected.replace('027RDC', '028RDC'), texts, mismatch_over_partial=True)['status'] == '不匹配'
    assert compare_seal_text_strict(expected.replace('①', '②'), texts, mismatch_over_partial=True)['status'] == '不匹配'
    registered = seal_candidates({'fields': {'签章要求': expected}, 'processing_artifacts': {'seals': artifacts}})
    assert registered[0]['text_status'] == '匹配'
    assert person not in registered[0]['texts'] and date not in registered[0]['texts']


@pytest.mark.parametrize('detected', ['日期2026年10月24日', '日期：2026-10-24', '收货人：张三'])
def test_detector_may_return_a_caption_joined_to_its_filled_value(detected):
    from receipt_ocr.recognition.seal.postprocess.rectangle_form import mark_receiving_form_fields
    a = receiving_form_with_values()
    a['rectangular_frame']['detected_boxes'][1]['text'] = detected
    mark_receiving_form_fields(a)
    assert a['reads'][3]['matching'] is False


@pytest.mark.parametrize('change', ['business_word', 'above_purpose', 'low_confidence', 'outside_cell', 'other_stamp'])
def test_only_independently_located_form_cells_are_excluded(change):
    from receipt_ocr.recognition.seal.postprocess.rectangle_form import mark_receiving_form_fields
    a = receiving_form_with_values(person='额外站点123', date='业务编号456')
    box = a['rectangular_frame']['detected_boxes'][1]
    if change == 'business_word':
        box['text'] = '日期科技有限公司'
    elif change == 'above_purpose':
        for point in box['points']:
            point[1] -= 300
    elif change == 'low_confidence':
        box['confidence'] = .89
    elif change == 'outside_cell':
        for point in box['points']:
            point[0] += 600
    else:
        a['reads'][1]['text'] = '业务受理'
    mark_receiving_form_fields(a)
    assert a['reads'][3].get('matching') is not False
