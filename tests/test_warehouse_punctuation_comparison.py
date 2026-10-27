"""A warehouse separator dot never relaxes another identity character."""
import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict, normalize_seal_text_strict


REQUIREMENT = '北京永乐DC010DCM收货专用章②'


@pytest.mark.parametrize('dot', ['.', '·', '．'])
def test_only_the_extra_warehouse_prefix_dot_is_ignored_in_comparison(dot):
    raw = f'北京永乐DC{dot} 010DCM收货专用章②'
    check = compare_seal_text_strict(REQUIREMENT, [raw])
    assert check['status'] == '匹配'
    assert check['recognized'] == raw and check['all_recognized'] == [raw]
    audit = check['field_comparison']['warehouse_title_punctuation']
    assert audit['raw_text'] == raw and audit['text'] == REQUIREMENT
    assert '分隔点' in check['message']
    assert normalize_seal_text_strict(raw) != normalize_seal_text_strict(REQUIREMENT)


@pytest.mark.parametrize('raw', [
    '北京永乐DC.010DCM收费专用章②',
    '北京永乐DC.010DCM收货专用章①',
    '北京永乐DC.020DCM收货专用章②',
    '北京朝阳DC.010DCM收货专用章②',
    '北京永乐DC01.0DCM收货专用章②',
    '北京·永乐DC010DCM收货专用章②',
    '北京永乐DC..010DCM收货专用章②',
    '北京永乐DC.010DCM收货专用章②公',
])
def test_dot_policy_does_not_hide_meaningful_code_name_purpose_or_number_differences(raw):
    check = compare_seal_text_strict(REQUIREMENT, [raw])
    assert check['status'] != '匹配'
    assert not (check.get('field_comparison') or {}).get('warehouse_title_punctuation')


def test_required_punctuation_and_numeric_periods_remain_literal():
    assert compare_seal_text_strict('北京永乐DC.010DCM收货专用章②', [REQUIREMENT])['status'] != '匹配'
    assert compare_seal_text_strict('金额100日期2026.01.02', ['金额1.00日期20260102'])['status'] != '匹配'
