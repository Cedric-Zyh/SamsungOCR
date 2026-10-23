from copy import deepcopy

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.rectangle_form import mark_empty_receiving_fields


def artifact():
    return {'schema_version': 2, 'region_id': 'seal-0', 'shape_code': 'rectangle',
            'rectangular_layout': 'frame_rows', 'rectangular_fixed_rows': [{}, {}, {}, {}],
            'inputs': [], 'errors': [], 'reads': []}


def test_empty_receiving_captions_remain_evidence_but_not_part_of_name(monkeypatch, tmp_path):
    a = artifact()
    words = ['广州花东仓020DCH', '收货专用章 ①', '收货人：', '收货日期：年 月 日']
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
                                '收货日期科技有限公司', '收货专用章（1）'])
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
