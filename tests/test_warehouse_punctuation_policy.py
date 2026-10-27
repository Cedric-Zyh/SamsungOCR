"""Only one extra dot at the complete warehouse code's initial digit boundary."""
from copy import deepcopy
from inspect import signature

import pytest

from receipt_ocr.domain.warehouse_punctuation_policy import warehouse_title_punctuation_adjustment


TITLE = '北京永乐DC010DCM'
REQUIREMENT = TITLE + '收货专用章②'
POLICY = 'warehouse_title_punctuation_from_requirement'


@pytest.mark.parametrize('dot', ['.', '·'])
def test_complete_identity_removes_only_the_single_requested_boundary_dot(dot):
    observed = REQUIREMENT.replace('DC010', 'DC' + dot + '010')
    result = warehouse_title_punctuation_adjustment(REQUIREMENT, observed)
    assert result == {
        'raw_text': observed, 'text': REQUIREMENT, 'position': 6,
        'code_index': 2, 'removed': dot, 'required_code': 'DC010DCM',
        'raw_code': 'DC' + dot + '010DCM', 'corrected_code': 'DC010DCM',
        'policy': POLICY,
        'changes': [{'index': 6, 'code_index': 2, 'before': dot, 'after': ''}],
    }


@pytest.mark.parametrize('dot', ['.', '·'])
def test_title_only_requires_the_complete_owner_and_complete_code(dot):
    observed = TITLE.replace('DC010', 'DC' + dot + '010')
    result = warehouse_title_punctuation_adjustment(REQUIREMENT, observed, title_only=True)
    assert result['text'] == TITLE and result['raw_text'] == observed
    assert result['required_code'] == 'DC010DCM'
    assert result['position'] == 6 and result['removed'] == dot
    assert warehouse_title_punctuation_adjustment(REQUIREMENT, observed) is None


def test_full_width_spacing_and_existing_simplified_script_semantics_are_retained():
    required = ' 北京永樂 ＤＣ０１０ＤＣＭ 收貨專用章（２） '
    observed = '\t北京永乐 DC · 010 DCM\n收货专用章 (2) '
    result = warehouse_title_punctuation_adjustment(required, observed)
    assert result['raw_text'] == observed
    assert result['text'] == '北京永乐DC010DCM收货专用章2'
    assert result['removed'] == '·' and result['position'] == 6
    assert result['raw_code'] == 'DC·010DCM'


def test_full_width_ascii_period_uses_the_existing_width_conversion_only():
    observed = REQUIREMENT.replace('DC010', 'ＤＣ．０１０')
    result = warehouse_title_punctuation_adjustment(REQUIREMENT, observed)
    assert result['raw_text'] == observed
    assert result['text'] == REQUIREMENT and result['removed'] == '.'


@pytest.mark.parametrize('required,observed', [
    ('上海仓A123BC收货章①', '上海仓A·123BC收货章①'),
    ('上海仓ABC123收货章(1)', '上海仓ABC.123收货章(1)'),
    ('上海仓dc010DCM收货章①', '上海仓dc·010DCM收货章①'),
])
def test_other_explicit_warehouse_letter_prefixes_are_literal(required, observed):
    result = warehouse_title_punctuation_adjustment(required, observed)
    assert result is not None
    assert result['raw_text'] == observed
    assert result['text'] == required.replace('(', '').replace(')', '')


@pytest.mark.parametrize('observed', [
    REQUIREMENT,
    ' 北京永乐 ＤＣ０１０ＤＣＭ 收货专用章② ',
    REQUIREMENT.replace('北京', '北·京'),
    REQUIREMENT.replace('永乐', '永.乐'),
    REQUIREMENT.replace('DC010', 'D·C010'),
    REQUIREMENT.replace('DC010', '.DC010'),
    REQUIREMENT.replace('DC010', 'DC0.10'),
    REQUIREMENT.replace('DC010', 'DC01·0'),
    REQUIREMENT.replace('DCM收', 'DC·M收'),
    REQUIREMENT.replace('DCM收', 'DCM.收'),
    REQUIREMENT.replace('收货专用章', '收货.专用章'),
    REQUIREMENT + '.',
    REQUIREMENT.replace('②', '(2.)'),
    REQUIREMENT.replace('DC010', 'DC..010'),
    REQUIREMENT.replace('DC010', 'DC··010'),
    REQUIREMENT.replace('DC010', 'DC.·010'),
    REQUIREMENT.replace('DC010', 'DC,010'),
    REQUIREMENT.replace('DC010', 'DC。010'),
    REQUIREMENT.replace('DC010', 'DC•010'),
    REQUIREMENT.replace('DC010', 'DC:010'),
    REQUIREMENT.replace('DC010', 'DC-010'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('北京', '上海'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('永乐', '永来'),
    REQUIREMENT.replace('DC010', 'DC·O10'),
    REQUIREMENT.replace('DC010', 'DC·020'),
    REQUIREMENT.replace('DC010', 'DC·01'),
    REQUIREMENT.replace('DC010', 'DC·0010'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('DCM', 'DCN'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('DCM', 'DcM'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('②', '①'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('②', '2'),
    REQUIREMENT.replace('DC010', 'DC·010').replace('收货专用章', '收货章'),
    REQUIREMENT.replace('DC010', 'DC·010') + '额外文字',
    '',
])
def test_name_digits_purpose_numbers_and_other_differences_remain_strict(observed):
    assert warehouse_title_punctuation_adjustment(REQUIREMENT, observed) is None


@pytest.mark.parametrize('observed', [
    'DC·010DCM', '010DCM', '永乐DC·010DCM', '北京永乐DC·010',
    '北京永乐DC·010DCM收货专用章', '北京永乐DC·010DCM②',
    '北京永乐DC·010DCM收货专用章②', '北京永乐DC·010DCM尾字',
])
def test_title_only_never_selects_fragments_or_drops_extra_identity_characters(observed):
    assert warehouse_title_punctuation_adjustment(REQUIREMENT, observed, title_only=True) is None


@pytest.mark.parametrize('required', [
    REQUIREMENT.replace('DC010', 'DC·010'),
    REQUIREMENT.replace('DC010', 'DC.010'),
])
def test_a_dot_in_the_requirement_is_not_authorized_for_removal(required):
    assert warehouse_title_punctuation_adjustment(required, REQUIREMENT) is None
    assert warehouse_title_punctuation_adjustment(required, required) is None
    observed = required.replace('010', '.010')
    assert warehouse_title_punctuation_adjustment(required, observed) is None


@pytest.mark.parametrize('dot', ['.', '·'])
def test_a_required_name_dot_is_preserved_while_only_the_code_boundary_dot_is_removed(dot):
    required = REQUIREMENT.replace('永乐', '永' + dot + '乐')
    observed = required.replace('DC010', 'DC·010')
    result = warehouse_title_punctuation_adjustment(required, observed)
    assert result['text'] == required
    assert result['raw_text'] == observed
    assert result['position'] == 7 and result['removed'] == '·'
    assert warehouse_title_punctuation_adjustment(required, observed.replace('永' + dot, '永')) is None


@pytest.mark.parametrize('required,observed', [
    ('北京服务中心站代码010DCM', '北京服务中心站代码DC·010DCM'),
    ('北京服务中心授权代码DC010DCM', '北京服务中心授权代码DC·010DCM'),
    ('北京公司ABC123收货专用章②', '北京公司ABC·123收货专用章②'),
    ('广州花东仓020DCH收货专用章①', '广州花东仓·020DCH收货专用章①'),
    ('北京仓DC-010DCM收货专用章②', '北京仓DC-·010DCM收货专用章②'),
    ('DC010DCM', 'DC·010DCM'),
    ('2026-10-27', '2026-10-.27'),
    ('2026.10.27', '2026.10..27'),
    ('金额123.45', '金额123..45'),
    ('12345', '123.45'),
    (REQUIREMENT + '/' + REQUIREMENT.replace('②', '①'),
     REQUIREMENT.replace('DC010', 'DC·010') + '/' + REQUIREMENT.replace('②', '①')),
])
def test_nonwarehouse_codes_dates_amounts_and_ambiguous_wordings_are_not_rewritten(required, observed):
    assert warehouse_title_punctuation_adjustment(required, observed) is None


def test_additional_fields_are_retained_and_cannot_be_cut_from_a_title():
    required = REQUIREMENT[:-1] + '电话:010-12345678②'
    observed = required.replace('DC010', 'DC·010')
    result = warehouse_title_punctuation_adjustment(required, observed)
    assert result['text'] == required
    assert warehouse_title_punctuation_adjustment(required, observed.replace('12345678', '12345679')) is None
    assert warehouse_title_punctuation_adjustment(required, TITLE.replace('DC010', 'DC·010'),
                                                   title_only=True) is None


@pytest.mark.parametrize('required', [None, '', '  ', 0, False, [], {}])
def test_no_nontext_or_missing_requirement_can_supply_a_deletion(required):
    assert warehouse_title_punctuation_adjustment(required, REQUIREMENT.replace('DC010', 'DC·010')) is None


@pytest.mark.parametrize('observed', [None, 0, False, [], {}])
def test_nontext_observation_is_not_a_warehouse_identity(observed):
    assert warehouse_title_punctuation_adjustment(REQUIREMENT, observed) is None


def test_api_and_returned_audits_are_independent_comparison_values():
    parameters = signature(warehouse_title_punctuation_adjustment).parameters
    assert list(parameters) == ['requirement', 'observed', 'title_only']
    assert parameters['title_only'].kind.name == 'KEYWORD_ONLY'
    observed = REQUIREMENT.replace('DC010', 'DC·010')
    before = deepcopy((REQUIREMENT, observed))
    first = warehouse_title_punctuation_adjustment(REQUIREMENT, observed)
    first['changes'].clear()
    second = warehouse_title_punctuation_adjustment(REQUIREMENT, observed)
    assert second['changes']
    assert (REQUIREMENT, observed) == before
