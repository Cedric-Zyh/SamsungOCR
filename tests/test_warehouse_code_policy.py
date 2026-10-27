"""Only the authorized warehouse-code 0/O comparison may use the requirement."""
from copy import deepcopy
from inspect import signature

import pytest

from receipt_ocr.domain.warehouse_code_policy import warehouse_code_adjustment


TITLE = '上海朱泾DC021DC0'
PURPOSE = '收货专用章'
REQUIREMENT = TITLE + PURPOSE + '①'
POLICY = 'warehouse_code_zero_o_from_requirement'


@pytest.mark.parametrize('required,observed', [
    (REQUIREMENT, REQUIREMENT.replace('DC0收', 'DCO收')),
    (REQUIREMENT.replace('DC0收', 'DCO收'), REQUIREMENT),
    (REQUIREMENT, REQUIREMENT.replace('DC021DC0', 'DCO21DCO')),
    ('广州花东仓020DCH收货专用章①', '广州花东仓O2ODCH收货专用章①'),
    ('北京永乐DC010DCM收货专用章①', '北京永乐DCO1ODCM收货专用章①'),
    ('上海仓A0B收货章(2)', '上海仓AOB收货章(2)'),
])
def test_only_warehouse_zero_o_changes_follow_the_requested_glyphs(required, observed):
    result = warehouse_code_adjustment(required, observed)
    assert result is not None
    assert result['raw_text'] == observed
    assert result['text'] == required.replace('(', '').replace(')', '')
    assert result['policy'] == POLICY
    assert result['corrected_code'] == result['required_code']
    assert result['raw_code'] != result['corrected_code']
    assert result['changes']
    for change in result['changes']:
        assert {change['before'], change['after']} == {'0', 'O'}
        assert result['raw_code'][change['code_index']] == change['before']
        assert result['corrected_code'][change['code_index']] == change['after']
        assert result['text'][change['index']] == change['after']


def test_original_width_spaces_and_punctuation_are_preserved_in_raw_text():
    required = ' 上海朱涇 ＤＣ０２１ＤＣ０ 收貨專用章（１） '
    observed = '\t上海朱泾 DC 021 DC O\n收货专用章 (1) '
    result = warehouse_code_adjustment(required, observed)
    assert result['raw_text'] == observed
    assert result['text'] == '上海朱泾DC021DC0收货专用章1'
    assert result['raw_code'] == 'DC021DCO'
    assert result['required_code'] == 'DC021DC0'
    assert result['changes'] == [{'index': 11, 'code_index': 7, 'before': 'O', 'after': '0'}]


@pytest.mark.parametrize('required,observed', [
    (REQUIREMENT, TITLE.replace('DC0', 'DCO')),
    (REQUIREMENT.replace('DC0收', 'DCO收'), TITLE),
    (' 上海朱泾 ＤＣ０２１ＤＣ０ 收货专用章（１） ', '上海朱泾 DC021DCO'),
])
def test_title_only_accepts_the_entire_name_and_code_first_row(required, observed):
    result = warehouse_code_adjustment(required, observed, title_only=True)
    assert result is not None
    assert result['text'] in {TITLE, TITLE[:-1] + 'O'}
    assert PURPOSE not in result['text']
    assert result['raw_text'] == observed


@pytest.mark.parametrize('observed', [
    'DC021DCO', '021DCO', '上海朱泾DCO', '朱泾DC021DCO',
    TITLE.replace('DC0', 'DCO') + PURPOSE,
    TITLE.replace('DC0', 'DCO') + PURPOSE + '①',
    TITLE.replace('DC0', 'DCO') + '①',
    TITLE.replace('DC0', 'DCO') + '额外文字',
])
def test_title_only_does_not_select_fragments_or_discard_extra_rows(observed):
    assert warehouse_code_adjustment(REQUIREMENT, observed, title_only=True) is None


@pytest.mark.parametrize('observed', [
    REQUIREMENT,
    ' 上海朱泾 ＤＣ０２１ＤＣ０ 收货专用章① ',
    REQUIREMENT.replace('DC0收', 'DCo收'),
    REQUIREMENT.replace('DC0收', 'DC〇收'),
    REQUIREMENT.replace('DC0收', 'DC⓪收'),
    REQUIREMENT.replace('DC0收', 'DC2收'),
    REQUIREMENT.replace('DC0收', 'DbO收'),
    REQUIREMENT.replace('DC0收', 'dcO收'),
    REQUIREMENT.replace('DC021DC0', 'DCO2IDCO'),
    REQUIREMENT.replace('021DC0', '031DCO'),
    REQUIREMENT.replace('DC0收', 'DC收'),
    REQUIREMENT.replace('DC0收', 'DCOO收'),
    REQUIREMENT.replace('DC0收', 'DCO收').replace('上海', '北京'),
    REQUIREMENT.replace('DC0收', 'DCO收').replace('朱泾', '朱径'),
    REQUIREMENT.replace('DC0收', 'DCO收').replace(PURPOSE, '收货章'),
    REQUIREMENT.replace('DC0收', 'DCO收').replace('①', '②'),
    REQUIREMENT.replace('DC0收', 'DCO收').replace('①', '1'),
    REQUIREMENT.replace('DC0收', 'DCO收') + '!',
    TITLE.replace('DC0', 'DCO'),
    PURPOSE + '①', '',
])
def test_no_other_letter_digit_name_purpose_number_or_length_change_is_permitted(observed):
    assert warehouse_code_adjustment(REQUIREMENT, observed) is None


@pytest.mark.parametrize('required,observed', [
    ('上海O公司021DC0收货专用章①', '上海0公司021DC0收货专用章①'),
    ('上海O公司021DC0收货专用章①', '上海0公司021DCO收货专用章①'),
    ('上海朱泾DC021DC0收货专用章(0)', '上海朱泾DC021DC0收货专用章(O)'),
    ('上海朱泾DC021DC0收货专用章(0)', '上海朱泾DC021DCO收货专用章(O)'),
    ('上海服务中心站代码021000', '上海服务中心站代码O21000'),
    ('上海服务中心授权代码021DC0', '上海服务中心授权代码021DCO'),
    ('上海公司021AB0收货专用章①', '上海公司021ABO收货专用章①'),
    ('上海仓库021000收货专用章①', '上海仓库O21000收货专用章①'),
    # An all-letter requirement has no independently parsed warehouse-code
    # field; the observation must not manufacture that field for the policy.
    ('上海仓AOB收货章(2)', '上海仓A0B收货章(2)'),
    ('021DC0', '021DCO'),
    ('上海仓021DC0/北京仓020DC0收货专用章①', '上海仓021DCO/北京仓020DC0收货专用章①'),
    ('上海仓021DC0收货专用章①/上海仓021DC0收货章①',
     '上海仓021DCO收货专用章①/上海仓021DC0收货章①'),
])
def test_nonwarehouse_fields_ambiguous_wording_and_multiple_wordings_are_not_corrected(required, observed):
    assert warehouse_code_adjustment(required, observed) is None


def test_additional_fields_must_remain_equal_and_cannot_be_dropped_for_title_mode():
    required = REQUIREMENT[:-1] + '电话:021-12345678①'
    observed = required.replace('DC0收', 'DCO收')
    assert warehouse_code_adjustment(required, observed) is not None
    assert warehouse_code_adjustment(required, observed.replace('12345678', '12345670')) is None
    assert warehouse_code_adjustment(required, TITLE.replace('DC0', 'DCO'), title_only=True) is None


@pytest.mark.parametrize('required', [None, '', '  ', 0, False, [], {}])
def test_missing_or_nontext_requirement_cannot_supply_a_correction(required):
    assert warehouse_code_adjustment(required, REQUIREMENT.replace('DC0收', 'DCO收')) is None


@pytest.mark.parametrize('observed', [None, 0, False, [], {}])
def test_nontext_observation_is_not_an_identity(observed):
    assert warehouse_code_adjustment(REQUIREMENT, observed) is None


def test_api_and_inputs_are_stable_comparison_only_values():
    parameters = signature(warehouse_code_adjustment).parameters
    assert list(parameters) == ['requirement', 'observed', 'title_only']
    assert parameters['title_only'].kind.name == 'KEYWORD_ONLY'
    observed = REQUIREMENT.replace('DC0收', 'DCO收')
    before = deepcopy((REQUIREMENT, observed))
    first = warehouse_code_adjustment(REQUIREMENT, observed)
    first['changes'].clear()
    second = warehouse_code_adjustment(REQUIREMENT, observed)
    assert second['changes']
    assert (REQUIREMENT, observed) == before
