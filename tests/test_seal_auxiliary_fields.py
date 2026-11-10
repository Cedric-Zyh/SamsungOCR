"""Extra phone/serial evidence never repairs required stamp identity."""
from copy import deepcopy

import pytest

from receipt_ocr.application.context import DocumentContext
from receipt_ocr.danzhengtong_seal import build_stage_result
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.providers.qingtong import compare_qingtong_seal


STATION = '三星售后6183342站'
PHONE = '0477-8345921'
COMPANY = '济南新宇航科技发展有限公司济宁分公司'
SERIAL = '3708023057021'


def compare(required, raw):
    return compare_seal_text_strict(required, [raw], mismatch_over_partial=True)


@pytest.mark.parametrize('raw', [
    STATION + ' ' + PHONE, STATION + '\n' + PHONE, STATION + PHONE,
    STATION + ' 电话：' + PHONE, STATION + ' TEL: ' + PHONE,
    STATION + ' ０４７７－８３４５９２１', STATION + ' 0477 8345921',
    STATION + ' 0477 - 8345921',
    STATION + ' 手机：13812345678', STATION + ' 13812345678',
])
def test_complete_extra_phone_matches_and_keeps_raw_reading(raw):
    result = compare(STATION, raw)
    assert result['status'] == '匹配' and result['reliable']
    assert result['recognized'] == raw and result['all_recognized'] == [raw]
    assert result['field_comparison']['additional_fields'] == ['电话']
    assert '附加信息' in result['message']


@pytest.mark.parametrize('suffix', [SERIAL, ' ' + SERIAL, ' 章号：' + SERIAL,
                                    ' 印章编号' + SERIAL, ' 编号:' + SERIAL,
                                    '(' + SERIAL + ')', '（ ' + SERIAL + ' ）', '章号:（' + SERIAL + '）'])
def test_extra_serial_matches_only_after_whole_body_matches(suffix):
    result = compare(COMPANY, COMPANY + suffix)
    assert result['status'] == '匹配'
    assert result['field_comparison']['additional_fields'] == ['章号']
    wrong = COMPANY.replace('济宁', '菏泽') + suffix
    assert compare(COMPANY, wrong)['status'] != '匹配'


@pytest.mark.parametrize('raw', [
    '三星售后6183343站 ' + PHONE, '三星售后618334站 ' + PHONE,
    '三星售前6183342站 ' + PHONE, STATION + '第二分公司 ' + PHONE,
    STATION + ' 合同专用章 ' + SERIAL, STATION + ' SAMSUNG ' + PHONE,
    STATION + ' (1) ' + SERIAL, STATION + ' ' + PHONE + '其他公司',
    STATION + ' 00477-8345921', STATION + ' 0477-834592',
    STATION + ' 0477-834592100', STATION + ' 1234567890123456',
    STATION + ' ABC' + SERIAL, STATION + ' 0477-8345921 0477-8345921',
])
def test_body_errors_extra_identity_and_ambiguous_numbers_do_not_pass(raw):
    assert compare(STATION, raw)['status'] != '匹配'


@pytest.mark.parametrize('raw', [
    STATION + '电话:04778345921', STATION + '电话:0477-8345921',
    STATION + ' TEL:0477 8345921', PHONE + STATION,
])
def test_required_phone_keeps_every_digit_but_ignores_formatting(raw):
    required = STATION + '电话:0477-8345921'
    assert compare(required, raw)['status'] == '匹配'
    assert compare(required, raw.replace('8345921', '8345922'))['status'] != '匹配'
    assert compare(required, STATION)['status'] != '匹配'


@pytest.mark.parametrize('actual', [SERIAL, '3708023057022', '370802305702', '', '37080230570210'])
def test_required_serial_is_mandatory_and_exact(actual):
    result = compare(COMPANY + '章号:' + SERIAL, COMPANY + (' ' + actual if actual else ''))
    assert (result['status'] == '匹配') is (actual == SERIAL)


def test_required_short_ordinal_and_use_are_not_removed():
    required = COMPANY + '收货专用章(1)'
    assert compare(required, required + ' ' + SERIAL)['status'] == '匹配'
    assert compare(required, COMPANY + '收货专用章(2) ' + SERIAL)['status'] != '匹配'
    assert compare(required, COMPANY + '收货专用章 ' + SERIAL)['status'] != '匹配'
    assert compare(required, COMPANY + '合同专用章(1) ' + SERIAL)['status'] != '匹配'


def test_phone_and_serial_can_be_added_without_joining_distinct_stamp_reads():
    raw = COMPANY + ' ' + PHONE + ' ' + SERIAL
    result = compare(COMPANY, raw)
    assert result['status'] == '匹配'
    assert set(result['field_comparison']['additional_fields']) == {'电话', '章号'}
    required = COMPANY + '收货专用章(1)'
    assert compare_seal_text_strict(required, [COMPANY, '收货专用章(1) ' + PHONE])['status'] != '匹配'


def test_danzhengtong_scene_uses_the_shared_policy_and_preserves_its_raw_response():
    raw = STATION + ' ' + PHONE
    item = {'index': 0, 'status': 'completed', 'region': {'role': '客户章'},
            'result': {'commitResult': {'收货客户印章': {'value': raw},
                       '印章颜色': {'value': '红色'}, '印章类型': {'value': '方章'}}}}
    before = deepcopy(item)
    result = build_stage_result(DocumentContext('unused.png'), {'签章要求': STATION}, [item])
    assert result['seal_check']['status'] == '匹配'
    assert result['seal_check']['recognized'] == raw
    assert result['seal_check']['field_comparison']['additional_fields'] == ['电话']
    assert item == before


def test_qingtong_ocr_uses_same_rule_without_repairing_its_template_channel():
    raw = STATION + ' ' + PHONE
    payload = {'ok': True, 'response': {'data': {'img_0': [{
        'matched_seal': {'label': '其他公司', 'similarity': .99},
        'text_formatted': raw, 'xyxy': [1, 2, 30, 40]}]}}}
    before = deepcopy(payload)
    result = compare_qingtong_seal(STATION, payload)
    assert result['status'] == '匹配'
    assert result['dual_check']['selected']['ocr']['matched'] is True
    assert result['dual_check']['selected']['template']['matched'] is False
    assert result['recognized'] == raw and payload == before
