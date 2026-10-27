from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.preprocess import code_phone
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone


CODE = '站代码：5988247'
PHONE = '02081769324'
COMPANY = '广州中启通信科技有限公司'


def fixture(tmp_path, monkeypatch, *, texts=None, scores=None, phones=1):
    source = tmp_path / 'source.png'
    image = np.full((400, 500, 3), 255, np.uint8)
    cv2.ellipse(image, (250, 200), (225, 170), 0, 0, 360, (40, 40, 255), 4)
    cv2.imwrite(str(source), image)
    boxes = [{'text': PHONE, 'confidence': .99,
              'points': [[125, 170], [390, 190], [385, 245], [120, 225]]}] * phones
    texts = texts or {}; scores = scores or {}
    def read(path, **kwargs):
        key = str(path).split('-')[-1].replace('.png', '')
        value = texts.get(key, {'telephone': PHONE, 'code': CODE, 'company': COMPANY}[key])
        return [TextObservation(value, scores.get(key, .99), 0, 0, 1, 1)]
    reader = SimpleNamespace(detect_boxes=lambda *a, **kw: boxes, read_line=read)
    monkeypatch.setattr(code_phone, 'trace_ellipse_border', lambda *a, **kw: None)
    monkeypatch.setattr(code_phone, '_fit_ellipse_annulus',
                        lambda image: [(1, 0, ((image.shape[1]/2, image.shape[0]/2),
                                               (image.shape[1]*.85, image.shape[0]*.80), 0))])
    return source, image, reader


def test_complete_independent_fields_prepare_their_own_ocr_inputs(tmp_path, monkeypatch):
    source, image, reader = fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    plan = code_phone.prepare_code_phone_layout(source, tmp_path/'out', 0,
                                                reader=reader, provider='paddle_v6')
    assert plan['method'] == 'ellipse_code_phone_rows'
    assert plan['reads']['code'].text == CODE
    assert plan['reads']['company'].text == COMPANY
    assert plan['orientation']['anchor_text'] == PHONE
    assert set(plan['paths']) == {'code', 'phone', 'company'}
    assert source.read_bytes() == before
    assert 'type_band_text' not in plan['orientation']


@pytest.mark.parametrize('phones', [0, 2])
def test_missing_or_ambiguous_telephone_cannot_select_layout(tmp_path, monkeypatch, phones):
    source, _, reader = fixture(tmp_path, monkeypatch, phones=phones)
    reader.read_line = lambda *a, **kw: pytest.fail('no unique phone')
    assert code_phone.prepare_code_phone_layout(source, tmp_path/'out', 0,
                                                reader=reader, provider='paddle_v6') is None


@pytest.mark.parametrize('texts,scores', [
    ({'telephone': '02081769325'}, {}),
    ({'code': '代码59882'}, {}),
    ({'code': '取机专用章'}, {}),
    ({'company': '广州中启'}, {}),
    ({}, {'telephone': .94}), ({}, {'code': .94}), ({}, {'company': .94}),
])
def test_incomplete_conflicting_or_weak_fields_keep_existing_processing(tmp_path, monkeypatch, texts, scores):
    source, _, reader = fixture(tmp_path, monkeypatch, texts=texts, scores=scores)
    assert code_phone.prepare_code_phone_layout(source, tmp_path/'out', 0,
                                                reader=reader, provider='paddle_v6') is None


def artifact():
    fields = [('code', CODE, 'layout_inner'), ('phone', PHONE, 'layout_inner'),
              ('company', COMPANY, 'ring')]
    return {
        'orientation': {'anchor_text': PHONE},
        'multi_zone_layout': {'method': 'ellipse_code_phone_rows'},
        'inputs': [{'id': name, 'zone_name': name} for name, _, _ in fields],
        'reads': [{'input_id': name, 'text': text, 'channel': channel,
                   'confidence': .99, 'image_url': '/'+name+'.png'} for name, text, channel in fields],
    }


def test_only_actual_same_stamp_fields_form_matching_text():
    a = artifact(); raw = deepcopy(a['reads'])
    assert finalize_multi_zone(a) == [CODE+PHONE+COMPANY]
    assert [r['text'] for r in a['reads']] == [r['text'] for r in raw]
    assert [r['zone_label'] for r in a['reads']] == ['上弧站代码', '横排电话', '下弧公司名称']
    assert a['phone_text'] == PHONE
    assert compare_seal_text_strict(CODE+PHONE+COMPANY, a['matching_texts'])['status'] == '匹配'
    assert compare_seal_text_strict(CODE.replace('247', '248')+PHONE+COMPANY,
                                    a['matching_texts'])['status'] == '不匹配'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'weak', 'wrong_phone', 'unknown'])
def test_postprocess_does_not_assemble_uncertain_or_extra_reads(change):
    a = artifact()
    if change == 'missing': a['reads'].pop()
    elif change == 'duplicate': a['reads'].append(deepcopy(a['reads'][0]))
    elif change == 'weak': a['reads'][0]['confidence'] = .94
    elif change == 'wrong_phone': a['reads'][1]['text'] = '02081769325'
    else: a['reads'].append({'input_id': 'unknown', 'text': '其他文字', 'confidence': .99})
    assert finalize_multi_zone(a) == []
    assert a['multi_zone_layout']['status'] == 'needs_review'


@pytest.mark.parametrize('selected', [0, 180, 90, 270])
def test_complete_fields_select_cardinal_direction_before_phone_fine_rotation(tmp_path, monkeypatch, selected):
    source, _, reader = fixture(tmp_path, monkeypatch)
    calls = []
    def candidate(path, *args, **kwargs):
        coarse = 0 if path == source else int(path.stem.rsplit('-', 1)[1])
        calls.append(coarse)
        if coarse == selected:
            return {'orientation': {'fine_rotation': -6.0, 'anchor_text': PHONE}}
        return None
    monkeypatch.setattr(code_phone, '_prepare_code_phone_candidate', candidate)
    result = code_phone.prepare_code_phone_layout(source, tmp_path/'out', 0,
                                                  reader=reader, provider='paddle_v6')
    assert result['orientation']['coarse_rotation'] == selected
    assert result['orientation']['rotation_steps'] == ([float(selected)] if selected else []) + [-6.0]
    assert calls == [0, 180, 90, 270][: [0, 180, 90, 270].index(selected)+1]
    assert result['orientation']['phone_direction_candidates'][-1]['complete']
