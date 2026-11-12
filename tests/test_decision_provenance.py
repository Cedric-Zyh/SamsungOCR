"""Decision versions survive replay without rewriting observations or human history."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from receipt_ocr.application.configured_recognition import ConfiguredRecognition
from receipt_ocr.application.deferred_document import combine_saved, empty_snapshot
from receipt_ocr.application.snapshots import DocumentSnapshot
from receipt_ocr.domain.decision_provenance import (
    PROVENANCE_KEY, RULE_VERSIONS, record_evaluation, standards_catalog_reference,
)
from receipt_ocr.domain.seal_standards import apply_standard_check
from receipt_ocr.provider_result_refresh import refresh_printed_fields_result, refresh_provider_result
from test_machine_check_replay import saved_document
from test_seal_standards import artifact, receipt, standard
from test_unified_recognition import unified


def test_live_plan_records_rules_and_snapshot_replay_keeps_their_origin(unified, monkeypatch):
    service, source, *_ = unified
    result = service.recognize(source, capture_assembly=True)
    provenance = result[PROVENANCE_KEY]
    assert provenance['origin'] == 'recorded'
    assert provenance['evaluations'][0]['operation'] == 'recognition'
    assert provenance['evaluations'][0]['rules']['seal_comparison'] == RULE_VERSIONS['seal_comparison']
    snapshot = result['_assembly_snapshot']
    assert snapshot[PROVENANCE_KEY] == provenance
    saved = DocumentSnapshot.from_json(json.loads(json.dumps(snapshot))).to_json()
    # The live-recognition fixture asserts its OCR resource scope in finalize;
    # saved replay deliberately runs without any OCR resource scope.
    from receipt_ocr.application import assembly, pipeline
    monkeypatch.setattr(assembly, 'complete_result', pipeline.complete_result)
    combined = combine_saved(source, 'receipt.png', saved)
    assert combined[PROVENANCE_KEY]['origin'] == 'recorded'
    assert combined[PROVENANCE_KEY]['evaluations'][:-1] == provenance['evaluations']
    assert combined[PROVENANCE_KEY]['evaluations'][-1]['operation'] == 'saved_reassembly'
    assert saved[PROVENANCE_KEY] == provenance


def test_changed_historical_requirement_keeps_old_rules_and_new_acceptance(monkeypatch):
    current = saved_document('seal', {'seal_check': {
        'requirement': '旧公司收货专用章', 'recognized': '测试有限公司收货专用章多',
        'status': '匹配', 'reliable': True, 'comparison_policy': 'strict_text',
    }}, '测试有限公司收货专用章')
    current['recognition_config']['acceptance'].update(reject_mode='any_mismatch', low_confidence_mode='ignore')
    record_evaluation(current, 'recognition', origin='recorded')
    before = deepcopy(current)
    monkeypatch.setitem(RULE_VERSIONS, 'seal_comparison', 'next-rule-version')
    result = refresh_printed_fields_result(current)
    events = result[PROVENANCE_KEY]['evaluations']
    assert result['seal_check']['status'] == '不匹配'
    assert events[0] == before[PROVENANCE_KEY]['evaluations'][0]
    assert events[-1]['rules']['seal_comparison'] == 'next-rule-version'
    assert events[-1]['acceptance']['reject_mode'] == 'any_mismatch'
    assert events[-1]['acceptance']['low_confidence_mode'] == 'ignore'
    assert result['recognition_variants'] == before['recognition_variants']
    assert current == before
    assert refresh_printed_fields_result(result) == result


def test_unknown_historical_rules_are_never_relabelled_as_fresh_recognition():
    current = saved_document('seal', {'seal_check': {
        'requirement': '旧公司收货专用章', 'recognized': '测试有限公司收货专用章多',
        'status': '匹配', 'reliable': True, 'comparison_policy': 'strict_text',
    }}, '测试有限公司收货专用章')
    result = refresh_printed_fields_result(current)
    assert result[PROVENANCE_KEY]['origin'] == 'unrecorded'
    assert [item['operation'] for item in result[PROVENANCE_KEY]['evaluations']] == ['printed_fields_refresh']
    assert PROVENANCE_KEY not in current


@pytest.mark.parametrize('human', [False, True])
def test_noop_and_human_protected_refresh_do_not_add_version_history(human):
    current = {'fields': {}, 'review_issues': [], 'review_reasons': [],
               'date_check': {'status': '未执行'}, 'seal_check': {'status': '未执行'}}
    if human:
        current.update(review_status='确认通过', human_note='已核对')
        current['seal_check'].update(status='匹配', human_confirmed_match=True)
    before = deepcopy(current)
    result = refresh_provider_result(current)
    assert result == before and current == before
    assert PROVENANCE_KEY not in result


def test_standard_revisions_are_recorded_only_at_an_explicit_evaluation_boundary():
    current = receipt(artifact())
    record_evaluation(current, 'recognition', origin='recorded')
    first = apply_standard_check(current, standard(), record_provenance=True)
    initial = deepcopy(first[PROVENANCE_KEY])
    latest = {**standard(), 'revision': 2}
    projected = apply_standard_check(first, latest)
    assert projected[PROVENANCE_KEY] == initial  # Read-time projection is not a saved evaluation.
    updated = apply_standard_check(first, latest, record_provenance=True)
    events = updated[PROVENANCE_KEY]['evaluations']
    assert events[-2]['standard']['revision'] == 1
    assert events[-1]['standard']['revision'] == 2
    assert apply_standard_check(updated, latest, record_provenance=True) == updated
    assert first[PROVENANCE_KEY] == initial
    missing = apply_standard_check(updated, None, record_provenance=True)
    assert missing[PROVENANCE_KEY]['evaluations'][-1]['standard'] == {'status': 'missing'}


def test_configured_recognition_distinguishes_frozen_catalog_from_applied_standard():
    catalog = [{**standard(), 'horizontal_text': 'present'}]
    initial = deepcopy(catalog)
    def recognize(*args, **kwargs):
        catalog[0]['revision'] = 2  # Another window updates the standard during recognition.
        return {**receipt(artifact()), '_assembly_snapshot': {'config': {}}}
    database = SimpleNamespace(get_json_setting=lambda *args: None,
        list_seal_standards=lambda: catalog, get_seal_standard=lambda requirement: catalog[0])
    service = ConfiguredRecognition(database, SimpleNamespace(seal_api=SimpleNamespace()),
        SimpleNamespace(recognize=recognize), lambda database: None)
    result = service.recognize('unused-input')
    events = result[PROVENANCE_KEY]['evaluations']
    assert events[0]['standards_catalog'] == standards_catalog_reference(initial)
    assert events[-1]['standard']['revision'] == 2
    assert result['_assembly_snapshot'][PROVENANCE_KEY] == result[PROVENANCE_KEY]


def test_context_history_is_stable_and_preserves_return_to_an_earlier_policy():
    result = {'recognition_config': {'acceptance': {'seal_match_mode': 'any', 'api_key': 'excluded'}}}
    record_evaluation(result, 'recognition', origin='recorded')
    record_evaluation(result, 'recognition', origin='recorded')
    assert len(result[PROVENANCE_KEY]['evaluations']) == 1
    result['recognition_config']['acceptance']['seal_match_mode'] = 'all'
    record_evaluation(result, 'recognition')
    result['recognition_config']['acceptance']['seal_match_mode'] = 'any'
    record_evaluation(result, 'recognition')
    assert [event['acceptance']['seal_match_mode'] for event in result[PROVENANCE_KEY]['evaluations']] == ['any', 'all', 'any']
    assert 'api_key' not in json.dumps(result[PROVENANCE_KEY])
    future = {PROVENANCE_KEY: {'schema_version': 99, 'unknown': ['keep']}}
    before = deepcopy(future)
    record_evaluation(future, 'recognition')
    assert future == before


def test_standard_catalog_fingerprint_ignores_order_but_tracks_policy_changes():
    first, second = standard(), {**standard(), 'id': 2}
    assert standards_catalog_reference([first, second]) == standards_catalog_reference([second, first])
    assert standards_catalog_reference([first]) != standards_catalog_reference([{**first, 'revision': 2}])


def test_persisted_repair_keeps_original_rule_context_and_audits_the_new_one(tmp_path, monkeypatch):
    from receipt_ocr.persistence.database import Database
    database = Database(tmp_path / 'rules.db')
    database.initialize()
    current = saved_document('seal', {'seal_check': {
        'requirement': '旧公司收货专用章', 'recognized': '测试有限公司收货专用章多',
        'status': '匹配', 'reliable': True, 'comparison_policy': 'strict_text',
    }}, '测试有限公司收货专用章')
    current.update(overall='通过', review_status='待复核')
    record_evaluation(current, 'recognition', origin='recorded')
    identity = database.insert_result(filename='one.png', stored_name='one.png',
        preview_name='', task_id='', result=current)
    monkeypatch.setitem(RULE_VERSIONS, 'seal_comparison', 'updated-rules')
    updated = refresh_printed_fields_result(current)
    database.result_writes.repair_saved_result(identity, result=updated, review_status='待复核',
        final_result=updated['overall'], action='识别规则更新')
    with database.connect() as connection:
        row = connection.execute('SELECT result_json,original_result_json FROM results WHERE id=?', (identity,)).fetchone()
        audit = connection.execute('SELECT before_json,after_json FROM review_history WHERE result_id=?', (identity,)).fetchone()
    original = json.loads(row['original_result_json'])
    saved = json.loads(row['result_json'])
    assert original[PROVENANCE_KEY] == current[PROVENANCE_KEY]
    assert saved[PROVENANCE_KEY]['evaluations'][-1]['rules']['seal_comparison'] == 'updated-rules'
    assert json.loads(audit['before_json'])[PROVENANCE_KEY] == current[PROVENANCE_KEY]
    assert json.loads(audit['after_json'])[PROVENANCE_KEY] == saved[PROVENANCE_KEY]


def deferred_inputs():
    config = {stage: [] for stage in ('fields', 'handwriting', 'date', 'seal', 'products')}
    config.update(date=['paddle_v6'], seal=['danzhengtong'], acceptance={'date_match_mode': 'any'})
    snapshot = empty_snapshot(config, {'要求到货': '2026-11-10', '签章要求': '测试客户收货章'})
    snapshot['results']['date'] = [{'method': 'paddle_v6', 'result': {
        'date_check': {'required': '2026-11-10', 'actual': '2026-11-10', 'status': '匹配', 'reliable': True},
        'date_ocr_texts': ['2026年11月10日'],
    }}]
    trace = {'mode': 'real', 'simulated': False, 'status': 'completed'}
    seal = {'trace': trace, 'requests': [{'status': 'completed', 'result': {'commitResult': {
        '收货客户印章': {'value': '测试客户收货章'}, '印章类型': {'value': '圆形'}, '印章颜色': {'value': '红色'},
    }}}]}
    return snapshot, seal


def test_seal_only_response_preserves_date_context_without_claiming_new_date_rules(tmp_path):
    snapshot, seal = deferred_inputs()
    before = deepcopy(snapshot)
    combined = combine_saved(tmp_path / 'unused.png', 'receipt.png', snapshot, remote_seal=seal)
    event = combined[PROVENANCE_KEY]['evaluations'][-1]
    assert event['scope'] == 'operation_context'
    assert event['new_remote_evidence'] is True
    assert set(event['rules']) == {'seal_comparison', 'evidence_selection', 'overall_decision'}
    assert combined['date_check']['actual'] == '2026-11-10'
    assert snapshot == before


@pytest.mark.parametrize('invalid', ['preparation', 'missing_trace', 'failed', 'simulated', 'error', 'pending', 'sender'])
def test_failed_or_unproven_seal_response_does_not_claim_new_remote_evidence(tmp_path, invalid):
    snapshot, seal = deferred_inputs()
    if invalid == 'preparation':
        seal['preparation_error'] = 'test preparation failed'
    elif invalid == 'missing_trace':
        seal.pop('trace')
    elif invalid == 'sender':
        seal['requests'][0]['region'] = {'role': '发货单位章'}
    elif invalid == 'failed':
        seal['requests'][0]['status'] = 'failed'
    else:
        seal['requests'][0][invalid] = True
    combined = combine_saved(tmp_path / 'unused.png', 'receipt.png', snapshot, remote_seal=seal)
    event = combined[PROVENANCE_KEY]['evaluations'][-1]
    assert event['new_remote_evidence'] is False
    assert set(event['rules']) == {'evidence_selection', 'overall_decision'}


@pytest.mark.parametrize('preserve', [False, True])
def test_fields_only_page_response_respects_saved_check_preservation(tmp_path, preserve):
    snapshot, _ = deferred_inputs()
    snapshot['config'].update(fields=['danzhengtong'], seal=[])
    remote = {'mode': 'real', 'simulated': False, 'status': 'completed',
              'result': {'commitResult': {'要求到货': {'value': '2026-11-11'}}}}
    combined = combine_saved(tmp_path / 'unused.png', 'receipt.png', snapshot, remote=remote,
                             preserve_provider_checks=preserve)
    event = combined[PROVENANCE_KEY]['evaluations'][-1]
    assert event['new_remote_evidence'] is True
    assert ('date_comparison' in event['rules']) is (not preserve)
    assert 'seal_comparison' not in event['rules']
    assert combined['date_check']['required'] == ('2026-11-10' if preserve else '2026-11-11')


def test_failed_page_response_retains_only_saved_reassembly_rule_context(tmp_path):
    snapshot, _ = deferred_inputs()
    snapshot['config']['date'] = ['paddle_v6', 'danzhengtong']
    remote = {'mode': 'real', 'simulated': False, 'status': 'failed', 'error': 'test timeout'}
    combined = combine_saved(tmp_path / 'unused.png', 'receipt.png', snapshot, remote=remote)
    event = combined[PROVENANCE_KEY]['evaluations'][-1]
    assert event['new_remote_evidence'] is False
    assert set(event['rules']) == {'evidence_selection', 'overall_decision'}


@pytest.mark.parametrize('unused', ['unselected_stage', 'missing_trace_metadata', 'invalid_payload'])
def test_unused_or_unproven_page_payload_is_not_recorded_as_new_evidence(tmp_path, unused):
    snapshot, _ = deferred_inputs()
    snapshot['config'].update(fields=['danzhengtong'], seal=[])
    remote = {'mode': 'real', 'simulated': False, 'status': 'completed',
              'result': {'commitResult': {'要求到货': {'value': '2026-11-10'}}}}
    if unused == 'unselected_stage':
        snapshot['config']['fields'] = []
    elif unused == 'missing_trace_metadata':
        remote.pop('mode')
    else:
        remote['result']['pending'] = True
    combined = combine_saved(tmp_path / 'unused.png', 'receipt.png', snapshot, remote=remote)
    event = combined[PROVENANCE_KEY]['evaluations'][-1]
    assert event['new_remote_evidence'] is False
    assert set(event['rules']) == {'evidence_selection', 'overall_decision'}
