import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict


BASE = '广州花东仓020DCH收货专用章'
REQUIREMENT = '广州花东仓 020DCH收货专用章 ②/①'


@pytest.mark.parametrize('actual', [BASE+'①', BASE+'②', BASE+' （①） '])
def test_either_explicit_number_matches_with_raw_text_and_requirement_preserved(actual):
    result = compare_seal_text_strict(REQUIREMENT, [actual])
    assert result['status'] == '匹配'
    assert result['reliable']
    assert result['score'] == result['requirement_coverage'] == 1.0
    assert result['recognized'] == actual
    assert result['all_recognized'] == [actual]
    assert result['requirement'] == REQUIREMENT
    assert result['comparison_policy'] == 'strict_information_fields'
    assert result['field_comparison']['allowed_numbers'] == ['②', '①']
    assert result['field_comparison']['matched_number'] in actual


@pytest.mark.parametrize('separator', ['/', '／', ' / ', ' ／ '])
def test_width_and_spaces_do_not_change_explicit_choice(separator):
    assert compare_seal_text_strict(BASE+'②'+separator+'①', [BASE+'①'])['status'] == '匹配'


@pytest.mark.parametrize('actual,status', [
    (BASE, '部分匹配'),
    (BASE+'③', '不匹配'),
    ('广州花西仓020DCH收货专用章①', '不匹配'),
    ('广州花东仓021DCH收货专用章①', '不匹配'),
    ('广州花东仓020DCH收货专用①', '部分匹配'),
    (BASE+'①②', '不匹配'),
    (BASE+'②/①', '不匹配'),
    (BASE+'(1)', '不匹配'),
])
def test_number_choice_does_not_hide_missing_or_conflicting_identity(actual, status):
    result = compare_seal_text_strict(REQUIREMENT, [actual])
    assert result['status'] == status
    assert result['recognized'] == actual
    assert not result.get('field_comparison')


def test_number_and_company_from_different_reads_cannot_be_combined():
    result = compare_seal_text_strict(REQUIREMENT, [BASE, '①'])
    assert result['status'] != '匹配'


@pytest.mark.parametrize('requirement,actual', [
    ('客户1收货章', '客户①收货章'),
    ('广州020/DCH收货专用章①', '广州020DCH收货专用章①'),
    ('客户收货专用章1/2', '客户收货专用章1'),
    ('客户收货专用章①③', '客户收货专用章①'),
    ('客户①/②服务有限公司收货专用章', '客户①服务有限公司收货专用章'),
    ('客户收货专用章①/②/3', '客户收货专用章①'),
])
def test_other_slashes_and_number_formats_remain_literal(requirement, actual):
    assert compare_seal_text_strict(requirement, [actual])['status'] != '匹配'


def test_station_suffix_is_still_required_after_optional_number():
    requirement = '客户取机专用章②/①5785258站'
    assert compare_seal_text_strict(requirement, ['客户取机专用章①5785258站'])['status'] == '匹配'
    assert compare_seal_text_strict(requirement, ['客户取机专用章①5785259站'])['status'] == '不匹配'


def test_conflicting_full_company_read_is_not_hidden_by_a_partial_fragment():
    result = compare_seal_text_strict(
        REQUIREMENT, ['广州花西仓020DCH收货专用章①', '收货专用章①'], mismatch_over_partial=True)
    assert result['status'] == '不匹配'


def test_fully_matching_candidate_wins_over_other_reads():
    result = compare_seal_text_strict(
        REQUIREMENT, [BASE+'③', BASE+'②'], mismatch_over_partial=True)
    assert result['status'] == '匹配'
    assert result['recognized'] == BASE+'②'
