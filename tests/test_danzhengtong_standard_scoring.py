"""Independent scoring must not disguise business acceptance as OCR accuracy."""
from copy import deepcopy

import pytest

from tools.analysis.seal.score_danzhengtong_standards import (
    canonical_shape, score_record, score_records,
)


def standard(**overrides):
    return {'id': 7, 'requirement': '测试（上海）有限公司收货专用章（1）',
            'recognized': '测试（上海）有限公司收货专用章（1）',
            'ocr_recognized': '测试（上海）有限公司收货专用章（1）',
            'recognized_source': 'ocr', 'shape': 'round', 'color': 'red', **overrides}


def response(text='测试(上海)有限公司 收货专用章(1)', *, color='红色', shape='圆章', status='completed'):
    return {'status': status, 'req_uuid': 'uuid-7', 'commitResult': {
        '收货客户印章': {'value': text, 'ratio': 1},
        '印章颜色': {'value': color}, '印章类型': {'value': shape}}}


def test_typography_only_agreement_preserves_raw_response_and_reference():
    reference, returned = standard(), response('测试（上海）有限公司\n收货专用章（１）')
    original_reference, original_returned = deepcopy(reference), deepcopy(returned)
    row = score_record(reference, returned)
    assert all(row['metrics'].values())
    assert row['raw_commitResult'] == original_returned['commitResult']
    assert row['returned_text'] == original_returned['commitResult']['收货客户印章']['value']
    assert reference == original_reference and returned == original_returned
    assert row['reference_is_verified_ground_truth'] is False
    assert row['baseline_flags'] == ['ocr_reference_unverified']


@pytest.mark.parametrize('text', [
    '测试(上海)有限公司收货专用章(2)',
    '测试(上海)有限公司收货专用章(1)克拉玛依',
    '测试(上海)有限公司收货专用章',
    '测试(北京)有限公司收货专用章(1)',
])
def test_number_place_and_missing_glyphs_are_real_text_errors(text):
    row = score_record(standard(), response(text))
    assert not row['metrics']['text_exact'] and not row['metrics']['all_three']
    assert row['differences'] and row['difference_description']
    assert row['returned_text'] == text


def test_business_phone_acceptance_does_not_inflate_exact_text_or_all_three():
    name = '三星手机（南方韵和海雅）服务中心维修专用章'
    raw = name + '维修热线：82533861'
    row = score_record(standard(requirement=name, recognized=name), response(raw))
    assert row['metrics']['business_match'] is True
    assert row['metrics']['text_exact'] is False and row['metrics']['all_three'] is False
    assert row['business_comparison']['field_comparison']['additional_fields'] == ['电话']
    assert row['raw_commitResult']['收货客户印章']['value'] == raw


@pytest.mark.parametrize('serial', ['3708023057021', '34030500000712', '210242003052667', '３７０８０２３０５７０２１'])
def test_extra_raw_trailing_seal_serial_matches_without_changing_exact_score(serial):
    name = '济南新宇航科技发展有限公司济宁分公司'
    raw = name + ' ' + serial + '\n'
    row = score_record(standard(recognized=name, requirement=name), response(raw))
    assert row['metrics']['text_match'] and row['metrics']['all_three']
    assert not row['metrics']['text_exact']
    assert row['text_match_kind'] == 'additional_serial'
    assert row['ignored_additional_serial'].isascii()
    assert row['returned_text'] == raw
    assert row['differences']  # Strict differences remain auditable.


@pytest.mark.parametrize('raw', [
    '济南新宇航科技发展有限公司济宁分公司 (1) 3708023057021',
    '济南新宇航科技发展有限公司济宁分公司 业务专用章 3708023057021',
    '济南新宇航科技发展有限公司济宁分公司 SAMSUNG',
    '济南新宇航科技发展有限公司济宁分公司 分公司',
    '济南新宇航科技发展有限公司济宁分公司 0477-8345921',
    '济南新宇航科技发展有限公司济宁分公司 25年8月30日',
    '济南新宇航科技发展有限公司济宁分公司 123456789012',
    '济南新宇航科技发展有限公司济宁分公司 1234567890123456',
    '3708023057021 济南新宇航科技发展有限公司济宁分公司',
    '济南新宇航科技发展有限公司济宁分公司 3708023 057021',
])
def test_only_independent_trailing_serial_is_allowed_not_arbitrary_additions(raw):
    name = '济南新宇航科技发展有限公司济宁分公司'
    row = score_record(standard(recognized=name, requirement=name), response(raw))
    assert not row['metrics']['text_match'] and not row['metrics']['all_three']
    assert row['text_match_kind'] == 'mismatch' and not row['ignored_additional_serial']


@pytest.mark.parametrize('expected,raw', [
    ('山东鑫舜禹电子科技有限公司', '山东鑫舜电子科技有限公司 3701057861393'),
    ('海口全人通讯器材有限公司', '连云港金人通讯器材有限公司 3202010996067'),
    ('测试有限公司收货专用章(1)', '测试有限公司收货专用章(2) 3202010996067'),
    ('测试有限公司 3708023057021', '测试有限公司 3708023057022'),
    ('测试有限公司 3708023057021', '测试有限公司'),
    ('测试有限公司370802305702', '测试有限公司3708023057021'),
])
def test_body_errors_and_reference_numbers_cannot_be_hidden_by_serial(expected, raw):
    row = score_record(standard(recognized=expected, requirement=expected), response(raw))
    assert not row['metrics']['text_match'] and not row['metrics']['all_three']


def test_summary_reports_user_matching_policy_separately_from_exact_agreement():
    name = '济南新宇航科技发展有限公司济宁分公司'
    scored = score_records([{'standard': standard(recognized=name, requirement=name),
                             'response': response(name + ' 3708023057021')}])
    assert scored['summary']['metrics']['text_match']['all_samples']['numerator'] == 1
    assert scored['summary']['metrics']['text_exact']['all_samples']['numerator'] == 0
    assert scored['summary']['additional_serial_match_count'] == 1
    assert scored['summary']['text_match_counts'] == {'additional_serial': 1}


def test_attached_ascii_identifier_is_not_treated_as_a_separate_serial():
    name = '仓库DC'
    row = score_record(standard(recognized=name, requirement=name), response(name + '3708023057021'))
    assert not row['metrics']['text_match']


@pytest.mark.parametrize('status', ['failed', 'waiting', 'queued', 'submission_unknown', 'rejected'])
def test_noncompleted_response_cannot_pass_even_with_stale_matching_fields(status):
    row = score_record(standard(), response(status=status))
    assert row['successful_return'] is False
    assert not any(row['metrics'].values())


@pytest.mark.parametrize('commit', [None, [], 'wrong', {'收货客户印章': 'scalar'},
                                    {'收货客户印章': {'value': ['list']}},
                                    {'收货客户印章': {'value': True}}, {'another_scene': {'value': 'text'}}])
def test_malformed_completed_response_is_invalid_and_not_success(commit):
    row = score_record(standard(), {'status': 'completed', 'commitResult': commit})
    assert row['outcome'] == 'invalid_response' and row['validation_errors']
    assert row['successful_return'] is False and not any(row['metrics'].values())
    assert row['raw_commitResult'] == commit


def test_empty_returns_and_empty_reference_never_match_by_empty_equality():
    for returned in ({'status': 'completed', 'commitResult': {}}, response('', color='', shape='')):
        row = score_record(standard(recognized=''), returned)
        assert row['outcome'] == 'empty_response'
        assert not row['successful_return'] and not any(row['metrics'].values())
    row = score_record(standard(recognized=''), response(''))
    assert row['successful_return'] and not row['metrics']['text_exact']
    assert not row['metrics']['all_three']
    assert 'empty_reference_text' in row['baseline_flags']


@pytest.mark.parametrize('shape,canonical', [('圆章', 'round'), ('圆形', 'round'), ('circle', 'round'),
                                            ('椭圆章', 'ellipse'), ('oval', 'ellipse'),
                                            ('长方形章', 'rectangle'), ('square', 'rectangle')])
def test_shape_aliases_match_saved_coarse_shapes(shape, canonical):
    assert canonical_shape(shape) == canonical
    row = score_record(standard(shape=canonical), response(shape=shape))
    assert row['metrics']['shape_match']


def test_missing_or_unknown_shape_color_are_not_equal_even_if_both_unknown():
    for color, shape in [('', ''), ('未知颜色', '未知类型')]:
        row = score_record(standard(color=color, shape=shape), response(color=color, shape=shape))
        assert not row['metrics']['color_match'] and not row['metrics']['shape_match']
        assert not row['metrics']['all_three']


def test_manual_reference_is_used_without_claiming_verified_ground_truth():
    row = score_record(standard(recognized_source='manual', ocr_recognized='旧错字'), response())
    assert row['expected_text'] == standard()['recognized'] and row['metrics']['text_exact']
    assert row['recognized_source'] == 'manual' and row['ocr_recognized'] == '旧错字'
    assert set(row['baseline_flags']) == {'manual_reference_unverified', 'reference_differs_from_saved_ocr'}


def test_reference_business_conflict_is_flagged_without_replacing_reference():
    reference = standard(recognized='测试(北京)有限公司收货专用章(1)')
    row = score_record(reference, response(reference['recognized']))
    assert row['metrics']['text_exact'] is True
    assert row['metrics']['business_match'] is False
    assert 'reference_vs_requirement_mismatch' in row['baseline_flags']
    assert row['reference_business_comparison']['status'] != '匹配'
    assert row['expected_text'] == reference['recognized']


def test_summary_keeps_complete_snapshot_and_valid_response_denominators_separate():
    inputs = [
        {'standard': standard(), 'response': response()},
        {'standard': standard(id=8), 'response': response(shape='椭圆章')},
        {'standard': standard(id=9), 'response': response(status='failed')},
        {'standard': standard(id=10), 'response': response(status='waiting')},
        {'standard': standard(id=11), 'response': {'status': 'completed', 'commitResult': {}}},
        {'standard': standard(id=12), 'response': {'status': 'completed', 'commitResult': []}},
    ]
    scored = score_records(inputs)
    summary = scored['summary']
    assert len(scored['rows']) == 6 and summary['total_count'] == 6
    assert summary['completed_status_count'] == 4 and summary['successful_return_count'] == 2
    assert summary['metrics']['all_three']['all_samples'] == {'numerator': 1, 'denominator': 6, 'rate': 1/6}
    assert summary['metrics']['all_three']['successful_returns'] == {'numerator': 1, 'denominator': 2, 'rate': .5}
    assert summary['outcome_counts'] == {'returned': 2, 'failed': 1, 'pending': 1, 'empty_response': 1, 'invalid_response': 1}
    assert '保存标准' in summary['title'] and '人工真值' in summary['baseline_note']


def test_zero_denominator_is_unavailable_not_perfect_accuracy():
    summary = score_records([])['summary']
    assert summary['metrics']['text_exact']['all_samples']['rate'] is None
    assert summary['metrics']['all_three']['successful_returns']['rate'] is None
