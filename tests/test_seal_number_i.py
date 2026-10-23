import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.parsing.seal_numbers import normalize_stamp_number_letters
from receipt_ocr.recognition.seal.preprocess.number import seal_number, merge_number_read
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone


@pytest.mark.parametrize('token', ['(i)', '（i）', '(I)', '（Ｉ）', '（ ｉ ）'])
def test_parenthesized_i_is_one_only_after_purpose_or_in_number_crop(token):
    raw = '深圳某供应链有限公司收货专用章' + token
    comparison = compare_seal_text_strict('深圳某供应链有限公司收货专用章（1）', [raw])
    assert comparison['status'] == '匹配'
    assert comparison['recognized'] == raw
    assert comparison['all_recognized'] == [raw]
    assert seal_number(token) == '1'


@pytest.mark.parametrize('text', ['i', 'I', '(ii)', '(l)', '(i1)', 'Ai公司', '仓库i站',
                                     '授权代码(i)', '某公司(i)', '电话(i)', '收货专用章i'])
def test_other_letters_and_fields_stay_literal(text):
    assert normalize_stamp_number_letters(text) == text
    assert not seal_number(text)


def test_compatibility_does_not_hide_wrong_digit_or_wrong_company():
    assert compare_seal_text_strict('甲公司收货专用章（2）', ['甲公司收货专用章（i）'])['status'] == '不匹配'
    assert compare_seal_text_strict('甲i公司收货专用章（1）', ['甲1公司收货专用章（i）'])['status'] == '不匹配'


def test_number_merge_keeps_raw_ocr_and_requires_existing_corroboration():
    artifact = {'region_id': 'seal-0', 'stamp_number_detection': {'status': 'detected', 'text': '(i)', 'confidence': .95},
                'reads': [{'channel': 'center', 'text': '收货专用章', 'confidence': .96, 'provider': 'paddle_v6', 'region_id': 'seal-0'},
                          {'channel': 'number', 'text': '(1)', 'confidence': .96, 'provider': 'paddle_v6', 'region_id': 'seal-0'}]}
    merge_number_read(artifact)
    assert artifact['stamp_number_status'] == 'merged'
    assert artifact['stamp_number_detection']['text'] == '(i)'
    artifact['stamp_number_detection']['text'] = '(2)'
    merge_number_read(artifact)
    assert artifact['stamp_number_status'] == 'unverified'


def test_independently_located_layout_number_accepts_i_without_changing_raw_read():
    artifact = {'multi_zone_layout': {'method': 'ellipse_continuous_name',
                 'expected_inner_roles': ['type', 'number'], 'arcs': {'company': {}}},
                'reads': [{'channel': ch, 'text': text, 'confidence': .99, 'image_url': '/test.png'}
                          for ch, text in [('layout_inner', '收货专用章'), ('layout_inner', '(i)'),
                                           ('ring', '深圳某供应链有限公司')]]}
    texts = finalize_multi_zone(artifact)
    assert texts == ['深圳某供应链有限公司收货专用章(i)']
    assert artifact['reads'][1]['text'] == '(i)'
    assert artifact['reads'][1]['zone_label'] == '章内编号'
    assert compare_seal_text_strict('深圳某供应链有限公司收货专用章(1)', texts)['status'] == '匹配'
    artifact['reads'][1]['confidence'] = .79
    assert finalize_multi_zone(artifact) == []
    assert artifact['reads'][1]['text'] == '(i)'
