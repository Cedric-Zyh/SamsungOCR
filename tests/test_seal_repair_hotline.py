"""A complete labelled repair hotline is contact information, not stamp identity."""
import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.parsing.seal_information_fields import repair_hotline_field_match


REQUIRED = '三星手机（南方韵和海雅）服务中心维修专用章'


def compare(text, requirement=REQUIRED):
    return compare_seal_text_strict(requirement, [text], mismatch_over_partial=True)


@pytest.mark.parametrize('raw', [
    '三星手机(南方韵和海雅)服务中心 维修专用章 维修热线:82533861',
    '三星手机(南方韵和海雅)服务中心 维修专用章 *维修热线:82533861*',
    REQUIRED + '维修热线：82533861',
    REQUIRED + '维修热线：0755-82533861',
    REQUIRED + '维修热线0755-82533861',
    REQUIRED + '维修热线：８２５３３８６１',
    REQUIRED + '＊维修热线：０７５５－８２５３３８６１＊',
    REQUIRED + '\n*维修热线:82533861*\n',
])
def test_explicit_complete_hotline_matches_without_rewriting_raw_ocr(raw):
    result = compare(raw)
    assert result['status'] == '匹配' and result['reliable'] is True
    assert result['recognized'] == raw and result['all_recognized'] == [raw]
    assert result['comparison_policy'] == 'strict_information_fields'
    assert result['field_comparison']['matched_fields'] == ['名称']
    assert result['field_comparison']['additional_fields'] == ['电话']
    assert repair_hotline_field_match(REQUIRED, raw) == result['field_comparison']


@pytest.mark.parametrize('tail', [
    '维修热线:', '维修热线', '维修热线:825338', '维修热线:8253386',
    '维修热线:825338611', '维修热线:0755-8253386', '维修热线:0755-825338611',
    '维修热线:075582533861', '维修热线:8253386O', '维修热线:82533861-1',
    '维修热线:82533861维修热线:82533861', '维修热线:82533861电话:82533861',
    '电话:82533861维修热线:82533861', '维修热线:82533861另一个公司',
    '维修热线:82533861分公司', '维修热线:82533861其他内容',
    '维修热线:82533861编号123', '维修热线:82533861授权代码:1234567',
    '维修热线:82533861地址:另一公司收货章',
    '维修热线:82533861*', '*维修热线:82533861', '**维修热线:82533861**',
    '*维修热线:82533861**', '**维修热线:82533861*',
    '*维修热线:82533861其他内容*', '维修热线:*82533861*',
])
def test_incomplete_duplicate_decorated_or_extra_identity_contact_text_does_not_pass(tail):
    raw = REQUIRED + tail
    assert compare(raw)['status'] != '匹配'
    assert repair_hotline_field_match(REQUIRED, raw) is None


@pytest.mark.parametrize('identity', [
    REQUIRED.replace('三星', '二星'), REQUIRED.replace('海雅', '海亚'),
    REQUIRED.replace('服务中心', '服务中心分公司'), REQUIRED.replace('维修专用章', '收货专用章'),
    REQUIRED.replace('维修专用章', '维修专用章2'), REQUIRED + '授权代码:1234567',
    '*' + REQUIRED, REQUIRED + '*', REQUIRED.replace('南方', '南*方'),
])
def test_phone_alias_and_stars_do_not_relax_company_branch_purpose_or_codes(identity):
    raw = identity + '*维修热线:82533861*'
    assert compare(raw)['status'] != '匹配'
    assert repair_hotline_field_match(REQUIRED, raw) is None


@pytest.mark.parametrize('required_label,actual_label', [
    ('电话:', '维修热线:'), ('维修热线:', '电话:'), ('维修热线：', '*维修热线:'),
])
def test_required_phone_is_mandatory_and_every_digit_must_match(required_label, actual_label):
    requirement = REQUIRED + required_label + '82533861'
    suffix = '*' if actual_label.startswith('*') else ''
    raw = REQUIRED + actual_label + '82533861' + suffix
    assert compare(raw, requirement)['status'] == '匹配'
    assert repair_hotline_field_match(requirement, raw)
    for wrong in (raw.replace('82533861', '82533862'), raw.replace('82533861', '8253386'), REQUIRED):
        assert compare(wrong, requirement)['status'] != '匹配'
        assert repair_hotline_field_match(requirement, wrong) is None


def test_authorization_code_still_matches_independently_and_cannot_be_hidden_in_phone():
    requirement = REQUIRED + '授权代码:1234567'
    raw = requirement + '维修热线:82533861'
    assert compare(raw, requirement)['status'] == '匹配'
    assert repair_hotline_field_match(requirement, raw)
    for wrong in (raw.replace('1234567', '1234568'), raw.replace('1234567', '123456'),
                  raw.replace('1234567', '12345670')):
        assert compare(wrong, requirement)['status'] != '匹配'
        assert repair_hotline_field_match(requirement, wrong) is None


@pytest.mark.parametrize('number', ['82533861', '0756-82533861', '0755-82533862'])
def test_required_area_code_and_phone_digits_are_not_removed_or_substituted(number):
    requirement = REQUIRED + '电话:0755-82533861'
    raw = REQUIRED + '维修热线:' + number
    assert compare(raw, requirement)['status'] != '匹配'
    assert repair_hotline_field_match(requirement, raw) is None


def test_history_repair_gate_is_specific_to_hotline_and_preserves_other_contacts():
    assert repair_hotline_field_match(REQUIRED, REQUIRED + '电话:82533861') is None
    assert repair_hotline_field_match(REQUIRED, REQUIRED + '维修热线:82533861地址:其他地址') is None
    assert compare(REQUIRED + '维修热线:82533861地址:其他地址')['status'] == '匹配'
    assert compare_seal_text_strict(REQUIRED, [REQUIRED.replace('三星', '二星'), '维修热线:82533861'])['status'] != '匹配'
