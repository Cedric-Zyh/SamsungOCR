"""Only the parenthesized token contributes from an isolated number input."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.parsing.seal_numbers import stamp_number_token
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.layout_fields import inner_role
from receipt_ocr.recognition.seal.preprocess.number import merge_number_read, seal_number
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone


@pytest.mark.parametrize('raw,token,number', [
    ('F(1)', '(1)', '1'), ('r(1)', '(1)', '1'), ('三(2)', '(2)', '2'),
    ('/(1)', '(1)', '1'), ('(1).', '(1)', '1'), ('(1))', '(1)', '1'),
    ('杂字（１２）噪声', '（１２）', '12'), ('a（01）.', '（01）', '01'),
    ('r(i)', '(i)', '1'), ('r(7).', '(7)', '7'),
])
def test_isolated_number_retains_brackets_and_contents_only(raw, token, number):
    assert stamp_number_token(raw) == token
    assert seal_number(raw, isolated=True) == number
    assert inner_role(raw, number_field=True) == 'number'


@pytest.mark.parametrize('raw', ['(1)(2)', '(1)字（1）', 'F1', '(1', '1)', '', '(ii)', '(l)', '(1i)'])
def test_no_number_choice_or_missing_bracket_is_invented(raw):
    assert inner_role(raw, number_field=True) != 'number'


def numbered(raw, *, confidence=.99):
    return {'multi_zone_layout': {'method': 'ellipse_continuous_name',
             'expected_inner_roles': ['type', 'number'], 'arcs': {'company': {}}},
            'reads': [{'channel': channel, 'text': text, 'confidence': confidence,
                       'image_url': f'/{index}.png'} for index, (channel, text) in enumerate([
                ('ring', '甲（中国）公司'), ('layout_inner', '收货专用章'), ('layout_inner', raw)])]}


@pytest.mark.parametrize('raw,token', [('F(1)', '(1)'), ('三(2)', '(2)'), ('(1).', '(1)'), ('r(i)', '(i)')])
def test_multi_zone_joins_clean_token_and_keeps_all_raw_observations(raw, token):
    artifact = numbered(raw)
    original = [(r['channel'], r['text'], r['image_url']) for r in artifact['reads']]
    texts = finalize_multi_zone(artifact)
    assert texts == ['甲（中国）公司收货专用章'+token]
    assert artifact['stamp_number_text'] == token
    assert [(r['channel'], r['text'], r['image_url']) for r in artifact['reads']] == original
    assert artifact['reads'][-1]['zone_label'] == '章内编号'


def test_wrong_number_and_low_confidence_are_not_turned_into_matches():
    artifact = numbered('r(7).')
    texts = finalize_multi_zone(artifact)
    assert texts == ['甲（中国）公司收货专用章(7)']
    assert compare_seal_text_strict('甲（中国）公司收货专用章(1)', texts)['status'] == '不匹配'
    assert finalize_multi_zone(numbered('F(1)', confidence=.79)) == []
    assert finalize_multi_zone(numbered('F(1)(2)')) == []


def test_whole_company_and_general_role_are_not_reduced_to_a_number():
    assert inner_role('甲公司(1)') != 'number'
    assert not seal_number('甲公司(i)')
    assert compare_seal_text_strict('乙公司收货专用章(1)', ['甲公司收货专用章(1)'])['status'] == '不匹配'


def test_separate_number_channel_uses_same_rule_with_existing_corroboration():
    artifact = {'region_id': 'seal-0', 'stamp_number_detection': {
        'status': 'detected', 'text': 'F(2)', 'confidence': .95}, 'reads': [
            {'channel': 'center', 'text': '收货专用章', 'provider': 'paddle_v6',
             'region_id': 'seal-0', 'confidence': .99},
            {'channel': 'number', 'text': '三(2).', 'provider': 'paddle_v6',
             'region_id': 'seal-0', 'confidence': .95}]}
    original = deepcopy(artifact['reads'])
    merge_number_read(artifact)
    assert artifact['round_type_band_text'] == '收货专用章(2)'
    assert artifact['stamp_number_text'] == '(2)'
    assert artifact['reads'] == original
    artifact['stamp_number_detection']['text'] = '(1)'
    merge_number_read(artifact)
    assert artifact['stamp_number_status'] == 'unverified'
