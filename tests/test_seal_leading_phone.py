"""A preceding phone is optional contact evidence, never a station correction."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.providers.qingtong import compare_qingtong_seal


REQUIRED = '三星售后6183342站'
PHONE = '0477-8345921'


def compare(text, required=REQUIRED):
    return compare_seal_text_strict(required, [text])


@pytest.mark.parametrize('prefix', [PHONE, PHONE+'\n', '电话：'+PHONE,
                                    '０４７７－８３４５９２１'])
def test_complete_leading_phone_does_not_change_required_identity(prefix):
    raw = prefix + REQUIRED
    result = compare(raw)
    assert result['status'] == '匹配'
    assert result['reliable'] is True
    assert result['recognized'] == raw
    assert result['all_recognized'] == [raw]
    assert result['field_comparison']['additional_fields'] == ['电话']
    assert result['comparison_policy'] == 'strict_information_fields'


@pytest.mark.parametrize('identity', [
    '三星售后6188342站', '三星售后6183542站', '三星售后618334站',
    '三星售后61833420站', '三星售后618O342站', '三星售前6183342站',
    '三星售后6183342站第二分公司', '三星售后6183342站授权代码:1234567',
])
def test_wrong_missing_or_additional_identity_never_passes(identity):
    assert compare(PHONE+identity)['status'] != '匹配'


@pytest.mark.parametrize('raw', [
    '0477-834592'+REQUIRED, '0477-834592100'+REQUIRED,
    '00477-8345921'+REQUIRED, '04778345921'+REQUIRED,
    'ABC'+PHONE+REQUIRED, PHONE+REQUIRED+'未知内容',
    PHONE+REQUIRED+'电话:0477-0000000',
])
def test_malformed_or_duplicate_contacts_are_not_silently_removed(raw):
    assert compare(raw)['status'] != '匹配'


def test_phone_is_mandatory_when_included_in_requirement():
    required = REQUIRED+'电话:'+PHONE
    assert compare(PHONE+REQUIRED, required)['status'] == '匹配'
    assert compare('0477-0000000'+REQUIRED, required)['status'] != '匹配'
    assert compare(REQUIRED, required)['status'] != '匹配'
    assert compare_seal_text_strict(required, [REQUIRED, PHONE])['status'] != '匹配'


def remote(raw):
    return {'ok': True, 'response': {'data': {'img_0': [{
        'matched_seal': {'label': '山东三星寄修中心', 'similarity': .74},
        'text_formatted': raw, 'xyxy': [1, 2, 30, 40],
    }]}}}


def test_qingtong_match_retains_raw_ocr_and_independent_template_verdict():
    raw = PHONE+REQUIRED
    payload = remote(raw);before = deepcopy(payload)
    result = compare_qingtong_seal(REQUIRED, payload)
    assert result['status'] == '匹配'
    assert result['recognized'] == raw
    assert result['source'] == '清瞳 · 印章文字 OCR'
    assert result['field_comparison']['additional_fields'] == ['电话']
    selected = result['dual_check']['selected']
    assert selected['ocr']['matched'] is True
    assert selected['ocr']['text'] == raw
    assert selected['template']['matched'] is False
    assert payload == before


@pytest.mark.parametrize('raw', [PHONE+'三星售后6188342站', PHONE+'三星售后6183542站'])
def test_screenshot_station_conflicts_remain_qingtong_mismatches(raw):
    result = compare_qingtong_seal(REQUIRED, remote(raw))
    assert result['status'] == '不匹配'
    assert result['dual_check']['selected']['ocr']['matched'] is False
    assert result['recognized'] == raw


def test_all_channel_mode_still_requires_template_match():
    result = compare_qingtong_seal(REQUIRED, remote(PHONE+REQUIRED), match_mode='all')
    assert result['status'] == '部分匹配'
    assert result['reliable'] is False
