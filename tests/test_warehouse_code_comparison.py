"""One authorized 0/O policy is shared by recognition, standards and sample views."""
from copy import deepcopy
import json
import sqlite3

import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict, normalize_seal_text_strict
from receipt_ocr.domain.sample_seal_decision import sample_seal_decision, sample_warehouse_code_adjustment
from receipt_ocr.domain.seal_standards import standard_comparison
from receipt_ocr.web.sample_result_filters import (
    LIST_RESULT_SQL, decode_list_result, sample_result_filters, project_sample_seal_result,
)
from receipt_ocr.web.sample_statistics import evaluate_sample


REQUIREMENT = '上海朱泾DC021DC0收货专用章①'
OBSERVED = '上海朱泾DC 021DCO收货专用章①'


def old_result(*, extra=None):
    check = {'status': '不匹配', 'recognized': OBSERVED, 'reliable': True, **(extra or {})}
    return {'fields': {'签章要求': REQUIREMENT}, 'seal_check': deepcopy(check),
            'recognition_config': {'seal': ['paddle_v6']},
            'recognition_variants': {'seal': [{'method': 'paddle_v6', 'details': {
                'seal_check': deepcopy(check)}}]},
            'processing_artifacts': {'seals': [{'shape_code': 'rectangle', 'color': 'red',
                'original_url': '/test/source.png', 'color_isolated_url': '/test/color.png',
                'rectangular_text': OBSERVED, 'reads': [{'channel': 'body', 'text': OBSERVED}]}]}}


@pytest.mark.parametrize('required,actual', [(REQUIREMENT, OBSERVED),
    (REQUIREMENT.replace('DC0收', 'DCO收'), OBSERVED.replace('DCO收', 'DC0收'))])
def test_directed_match_keeps_real_ocr_and_explains_which_required_code_was_used(required, actual):
    result = compare_seal_text_strict(required, [actual])
    assert result['status'] == '匹配' and result['reliable'] is True
    assert result['recognized'] == actual and result['all_recognized'] == [actual]
    assert result['comparison_policy'] == 'strict_information_fields'
    policy = result['field_comparison']['warehouse_code_adjustment']
    assert policy['raw_text'] == actual
    assert policy['policy'] == 'warehouse_code_zero_o_from_requirement'
    assert policy['required_code'] == policy['corrected_code']
    assert '按签章要求' in result['message']
    assert normalize_seal_text_strict(required) != normalize_seal_text_strict(actual)


@pytest.mark.parametrize('actual', [OBSERVED.replace('021', '031'), OBSERVED.replace('①', '②'),
    OBSERVED.replace('朱泾', '金山'), OBSERVED.replace('DCO', 'DbO'),
    OBSERVED.replace('DCO', 'DGO'), OBSERVED.replace('DCO', 'DCo'),
    OBSERVED + '公', OBSERVED.replace('收货专用章', '收货章')])
def test_other_differences_do_not_receive_a_requirement_code_correction(actual):
    result = compare_seal_text_strict(REQUIREMENT, [actual])
    assert result['status'] != '匹配'
    assert not (result.get('field_comparison') or {}).get('warehouse_code_adjustment')


def test_existing_omission_and_nonwarehouse_identity_rules_remain_literal():
    assert compare_seal_text_strict('甲O公司收货专用章①', ['甲0公司收货专用章①'])['status'] == '不匹配'
    assert compare_seal_text_strict('维修中心站代码O21', ['维修中心站代码021'])['status'] == '不匹配'
    assert compare_seal_text_strict(REQUIREMENT, [REQUIREMENT[:-1]])['status'] == '部分匹配'


def test_same_raw_warehouse_word_is_consistent_in_history_filters_statistics_and_detail():
    result = old_result(); before = deepcopy(result)
    truth = {'fields': {'签章要求': REQUIREMENT}, 'seal_should_match': True}
    job = {'status': 'succeeded', 'result_id': 1, 'result_json': json.dumps(result)}
    filters = sample_result_filters(job, truth, result=result)
    assert filters['seals']['local:paddle_v6']['state'] == '匹配'
    assert filters['seals']['combined']['state'] == '匹配'
    stats = evaluate_sample('test.jpg', truth, job)
    assert stats['seals']['local:paddle_v6']['decision'] == '匹配'
    assert stats['seals']['local:paddle_v6']['value'] == OBSERVED
    detail = project_sample_seal_result(result)
    assert detail['seal_check']['status'] == '匹配'
    assert detail['seal_check']['recognized'] == OBSERVED
    assert '按签章要求' in detail['seal_check']['message']
    assert detail['recognition_variants']['seal'][0]['details']['seal_check']['field_comparison']
    assert detail['processing_artifacts'] == before['processing_artifacts']
    assert result == before


@pytest.mark.parametrize('extra', [{'human_confirmed_match': False},
                                  {'comparison_policy': 'manual_standard_text_and_shape'}])
def test_historical_human_or_shape_rejection_is_not_overridden(extra):
    check = {'status': '不匹配', 'recognized': OBSERVED, **extra}
    assert sample_seal_decision(check, requirement=REQUIREMENT) == '不匹配'
    result = old_result(extra=extra)
    assert sample_result_filters({'status': 'succeeded'}, {}, result=result)['seals']['combined']['state'] == '不匹配'


def test_current_requirement_controls_the_narrow_historical_rule():
    check = {'status': '不匹配', 'recognized': OBSERVED, 'requirement': '旧仓库收货章'}
    assert sample_seal_decision(check, requirement=REQUIREMENT) == '匹配'


def test_text_policy_cannot_bypass_standard_shape_rejection():
    result = old_result()
    matched = standard_comparison(result, {'id': 1, 'shape': 'rectangle', 'color': 'red'})
    assert matched['status'] == 'matched'
    assert '按签章要求' in matched['candidates'][0]['text_match_message']
    wrong_shape = standard_comparison(result, {'id': 2, 'shape': 'round', 'color': 'red'})
    assert wrong_shape['status'] == 'unmatched'
    assert wrong_shape['candidates'][0]['text_status'] == '匹配'


def test_historical_remote_ocr_and_template_explain_only_their_own_adjustment():
    comparison = {'status': '不匹配', 'recognized': OBSERVED}
    remote = {'status': '不匹配', 'recognized': OBSERVED, 'dual_check': {'selected': {
        'template': {'label': OBSERVED, 'requirement_match': deepcopy(comparison)},
        'ocr': {'text': OBSERVED.replace('021', '031'), 'comparison': {
            'status': '不匹配', 'recognized': OBSERVED.replace('021', '031')}}}}}
    result = {'fields': {'签章要求': REQUIREMENT}, 'seal_check': remote,
              'recognition_config': {'seal': ['qingtong']}}
    original = deepcopy(result)
    view = project_sample_seal_result(result)
    selected = view['seal_check']['dual_check']['selected']
    assert selected['template']['status'] == '匹配'
    assert '按签章要求' in selected['template']['requirement_match']['message']
    assert selected['ocr']['comparison']['status'] == '不匹配'
    assert not selected['ocr']['comparison'].get('field_comparison')
    assert result == original


@pytest.mark.parametrize('status', ['未识别', '需人工复核', '待确认', '匹配待确认', '匹配'])
def test_blocked_receiving_display_candidate_cannot_bypass_a_different_numeric_read(status):
    result = old_result(extra={
        'status': status, 'reliable': False,
        'receiving_review': {'required': True, 'reasons': ['primary_meaningful_conflict']},
    })
    stamp = result['processing_artifacts']['seals'][0]
    stamp['receiving_form'] = {
        'managed': True, 'status': 'conflict', 'identity_text': '',
        'candidate_text': OBSERVED, 'reason': 'primary_meaningful_conflict',
        'primary': {'conflicts': [{'reason': 'disagreeing_whole_row_observations',
                                  'observations': [{'text': OBSERVED.replace('021', '031'),
                                                    'confidence': .99}]}]},
    }
    before = deepcopy(result)
    check = result['seal_check']
    assert sample_warehouse_code_adjustment(check, requirement=REQUIREMENT) is None
    assert sample_seal_decision(check, requirement=REQUIREMENT) == '部分匹配'
    truth = {'fields': {'签章要求': REQUIREMENT}, 'seal_should_match': True}
    job = {'status': 'succeeded', 'result_json': json.dumps(result)}
    assert sample_result_filters(job, truth, result=result)['seals']['combined']['state'] == '部分匹配'
    assert evaluate_sample('blocked.jpg', truth, job)['seals']['local:paddle_v6']['decision'] == '部分匹配'
    view = project_sample_seal_result(result)
    assert view['seal_check']['status'] == '部分匹配'
    assert view['seal_check']['reliable'] is False
    assert not view['seal_check'].get('field_comparison')
    assert view['processing_artifacts'] == before['processing_artifacts']
    assert result == before


def test_adopted_receiving_identity_still_uses_the_authorized_code_policy():
    result = old_result(extra={'receiving_review': {'required': False}, 'reliable': False})
    stamp = result['processing_artifacts']['seals'][0]
    stamp.update(receiving_form={'managed': True, 'status': 'adopted', 'identity_text': OBSERVED},
                 matching_texts=[OBSERVED])
    before = deepcopy(result)
    assert sample_warehouse_code_adjustment(result['seal_check'], requirement=REQUIREMENT)
    assert sample_seal_decision(result['seal_check'], requirement=REQUIREMENT) == '匹配'
    truth = {'fields': {'签章要求': REQUIREMENT}, 'seal_should_match': True}
    job = {'status': 'succeeded', 'result_json': json.dumps(result)}
    assert sample_result_filters(job, truth, result=result)['seals']['combined']['state'] == '匹配'
    assert evaluate_sample('adopted.jpg', truth, job)['seals']['local:paddle_v6']['decision'] == '匹配'
    view = project_sample_seal_result(result)
    assert view['seal_check']['status'] == '匹配' and view['seal_check']['reliable'] is True
    assert '按签章要求' in view['seal_check']['message']
    assert view['processing_artifacts'] == before['processing_artifacts']
    assert result == before


def _list_result(result):
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE results(result_json TEXT)')
        con.execute('INSERT INTO results VALUES(?)', (json.dumps(result),))
        compact = con.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0]
    return decode_list_result(compact)


def test_compact_listing_retains_receiving_block_and_sqlite_boolean_semantics():
    result = old_result(extra={'status': '未识别', 'reliable': False,
                               'receiving_review': {'required': True}})
    compact = _list_result(result)
    check = compact['seal_check']
    assert check['reliable'] == 0 and check['receiving_review']['required'] == 1
    assert sample_warehouse_code_adjustment(check, requirement=REQUIREMENT) is None
    assert sample_seal_decision(check, requirement=REQUIREMENT) == '部分匹配'
    full = sample_result_filters({'status': 'succeeded'}, {}, result=result)
    listed = sample_result_filters({'status': 'succeeded'}, {}, result=compact)
    assert listed == full
    assert listed['seals']['combined']['state'] == '部分匹配'


def test_compact_listing_retains_the_simulated_reading_guard():
    result = _list_result(old_result(extra={'simulated': True}))
    assert result['seal_check']['simulated'] == 1
    assert sample_warehouse_code_adjustment(result['seal_check'], requirement=REQUIREMENT) is None
    assert sample_seal_decision(result['seal_check'], requirement=REQUIREMENT) == '不匹配'


@pytest.mark.parametrize('another_channel_matches', [False, True])
def test_old_text_and_shape_review_cannot_pass_through_text_policy_or_another_channel(another_channel_matches):
    # Before this code policy, a DCO reading and wrong physical shape both
    # failed. apply_standard_check saved the aggregate as 需人工复核 because
    # its old text check was not yet a match, despite a measured shape clash.
    result = old_result(extra={'status': '需人工复核', 'reliable': False,
                               'comparison_policy': 'manual_standard_text_and_shape', 'standard_id': 1})
    result['seal_standard_check'] = {
        'status': 'unmatched', 'requirement': REQUIREMENT,
        'standard': {'id': 1, 'shape': 'round', 'color': 'red'},
        'candidates': [{'text_status': '不匹配', 'shape_status': '不匹配', 'matched': False}],
    }
    if another_channel_matches:
        result['recognition_variants']['seal'][0]['details']['seal_check'] = {
            'status': '匹配', 'recognized': REQUIREMENT, 'reliable': True,
        }
    before = deepcopy(result)
    assert sample_seal_decision(result['seal_check'], requirement=REQUIREMENT) == '部分匹配'
    for source in (result, _list_result(result)):
        filtered = sample_result_filters({'status': 'succeeded'}, {}, result=source)
        assert filtered['seals']['combined']['state'] == '部分匹配'
        if another_channel_matches:
            assert filtered['seals']['local:paddle_v6']['state'] == '匹配'
    view = project_sample_seal_result(result)
    assert view['seal_check']['status'] == '部分匹配'
    assert view['seal_check']['reliable'] is False
    assert view['seal_standard_check']['status'] == 'unmatched'
    assert result == before


def test_an_already_matching_standard_is_not_downgraded():
    result = old_result(extra={'status': '匹配', 'reliable': True,
                               'comparison_policy': 'manual_standard_text_and_shape', 'standard_id': 1})
    assert sample_seal_decision(result['seal_check'], requirement=REQUIREMENT) == '匹配'
    for source in (result, _list_result(result)):
        assert sample_result_filters({'status': 'succeeded'}, {}, result=source)['seals']['combined']['state'] == '匹配'
    view = project_sample_seal_result(result)
    assert view['seal_check']['status'] == '匹配' and view['seal_check']['reliable'] is True


@pytest.mark.parametrize('status', ['不匹配', '需人工复核', '匹配待确认', '匹配'])
def test_explicit_human_rejection_cannot_become_a_matching_text_decision(status):
    result = old_result(extra={'status': status, 'human_confirmed_match': False})
    result['recognition_variants']['seal'][0]['details']['seal_check'] = {
        'status': '匹配', 'recognized': REQUIREMENT, 'reliable': True,
    }
    assert sample_seal_decision(result['seal_check'], requirement=REQUIREMENT) == '不匹配'
    for source in (result, _list_result(result)):
        assert sample_result_filters({'status': 'succeeded'}, {}, result=source)['seals']['combined']['state'] == '不匹配'
    assert project_sample_seal_result(result)['seal_check']['status'] == '不匹配'
