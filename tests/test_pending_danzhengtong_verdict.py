"""Pending remote evidence may defer a verdict, never regular completion."""
from copy import deepcopy

import pytest

from receipt_ocr.application.deferred_document import mark_danzhengtong_pending
from receipt_ocr.domain.seal_standards import apply_standard_check
from test_seal_standards import receipt, artifact, standard


def plan(**policy):
    config = {stage: ['paddle_v6', 'danzhengtong'] for stage in ('fields', 'handwriting', 'date', 'seal')}
    config['products'] = []
    config['acceptance'] = {'date_match_mode': 'any', 'seal_match_mode': 'any',
                            'signature_match_mode': 'none', 'seal_pass_standard': 'any_exact',
                            'low_confidence_mode': 'ignore', 'reject_mode': 'any_mismatch', **policy}
    return config


def test_existing_complete_match_passes_while_all_remote_stages_remain_pending():
    original = receipt(artifact())
    pending = mark_danzhengtong_pending(original, plan())
    assert pending['overall'] == '通过'
    assert pending['date_check'] == original['date_check']
    assert pending['seal_check'] == original['seal_check']
    assert all(pending['recognition_variants'][stage][-1]['pending']
               for stage in ('fields', 'handwriting', 'date', 'seal'))
    projected = apply_standard_check(pending, standard())
    assert projected['overall'] == projected['final_result'] == '通过'
    assert not any(issue['code'] == 'provider_pending' for issue in projected['review_issues'])
    assert 'recognition_variants' not in original


@pytest.mark.parametrize('policy', [
    {'seal_match_mode': 'all'}, {'date_match_mode': 'all'},
    {'signature_match_mode': 'all'}, {'seal_pass_standard': 'danzhengtong_exact'},
    {'date_source': 'danzhengtong', 'date_match_mode': None},
])
def test_required_remote_result_waits_even_when_available_checks_match(policy):
    pending = mark_danzhengtong_pending(receipt(artifact()), plan(**policy))
    assert pending['overall'] == pending['final_result'] == '待结果'
    assert pending['review_status'] == '待复核'
    assert apply_standard_check(pending, standard())['overall'] == '待结果'


@pytest.mark.parametrize('state,reliable', [('不匹配', True), ('未识别', False), ('需人工复核', False)])
def test_incomplete_or_mismatching_evidence_waits_without_premature_rejection(state, reliable):
    original = receipt(artifact())
    original.update(overall='不通过', final_result='不通过')
    original['date_check'].update(status=state, reliable=reliable)
    pending = mark_danzhengtong_pending(original, plan())
    assert pending['overall'] == pending['final_result'] == '待结果'
    assert apply_standard_check(pending, standard())['overall'] == '待结果'


def test_shape_standard_still_applies_before_any_early_pass_and_can_release_pending():
    pending = mark_danzhengtong_pending(receipt(artifact()), plan())
    missing = apply_standard_check(pending, None)
    assert missing['overall'] == '待结果'
    assert missing['seal_standard_check']['status'] == 'missing'
    again = apply_standard_check(missing, None)
    assert len([issue for issue in again['review_issues'] if issue['code'] == 'provider_pending']) == 1
    assert apply_standard_check(again, standard())['overall'] == '通过'


def test_disabled_remote_source_creates_neither_pending_status_nor_dependency():
    config = plan(seal_match_mode='all', date_match_mode='all')
    for stage, methods in config.items():
        if isinstance(methods, list):
            config[stage] = [method for method in methods if method != 'danzhengtong']
    original = receipt(artifact())
    assert mark_danzhengtong_pending(original, config)['overall'] == '通过'
    assert all(not variants for variants in mark_danzhengtong_pending(original, config).get('recognition_variants', {}).values())


def test_all_match_requirement_only_waits_for_remote_sources_in_that_stage():
    config = plan(seal_match_mode='all', date_match_mode='all')
    config['date'] = config['seal'] = ['paddle_v6']
    assert mark_danzhengtong_pending(receipt(artifact()), config)['overall'] == '通过'


def test_explicit_human_verdict_remains_authoritative_while_remote_queries_continue():
    original = receipt(artifact())
    original.update(review_status='确认通过', human_note='人工已核验')
    pending = mark_danzhengtong_pending(original, plan(seal_match_mode='all'))
    assert apply_standard_check(pending, None)['review_status'] == '确认通过'
    assert pending['overall'] == '通过'
    assert pending['human_note'] == '人工已核验'


def test_legacy_review_warning_is_not_lost_when_pending_verdict_is_reprojected():
    original = receipt(artifact())
    original.pop('review_issues')
    original.update(overall='需人工复核', review_reasons=['字段需要人工确认'])
    pending = mark_danzhengtong_pending(original, plan())
    projected = apply_standard_check(pending, standard())
    assert projected['overall'] == '待结果'
    assert '字段需要人工确认' in projected['review_reasons']
