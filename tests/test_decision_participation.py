"""Comparison evidence remains visible independently of overall pass criteria."""
from copy import deepcopy

import pytest

from receipt_ocr.application.assembly import assemble_result
from receipt_ocr.domain.decision import finalize_result
from receipt_ocr.domain.fields.schema import derive_signature_check, project_fields
from receipt_ocr.domain.issues import issue
from receipt_ocr.domain.stages import STAGES
from test_printed_field_pipeline import context


def receipt():
    return {
        'fields': {'仓库联系人': '赵立新、张小虎', '仓库接收人': '赵建新'},
        'date_check': {'status': '匹配', 'actual': '2026-09-20', 'reliable': True},
        'seal_check': {'status': '匹配', 'recognized': '客户收货章', 'reliable': True},
        'recognition_config': {'acceptance': {'date_match_mode': 'any', 'seal_match_mode': 'any',
            'signature_match_mode': 'none', 'low_confidence_mode': 'ignore', 'reject_mode': 'any_mismatch'}},
        'review_issues': [],
    }


@pytest.mark.parametrize('scope', ['fields', 'handwriting'])
def test_screenshot_receipt_passes_with_signature_mismatch_kept_as_evidence(scope):
    record = receipt()
    record['signature_check'] = {**record['fields'], 'status': '未核对', 'reliable': True,
                                 'source': '按设置跳过', 'match_mode': 'none'}
    record['review_issues'] = [issue('provider_conflict', scope, '多路识别结果不同')]
    finalize_result(record)
    assert record['overall'] == record['final_result'] == '通过'
    assert record['review_status'] == '无需复核'
    assert record['review_reasons'] == []
    assert record['signature_check']['status'] == '不匹配'
    assert record['signature_check']['source'] == '字段比对'
    assert project_fields(deepcopy(record))['signature_check'] == record['signature_check']


@pytest.mark.parametrize('stage', ['date', 'seal'])
@pytest.mark.parametrize('status,reliable', [('不匹配', True), ('未识别', False)])
def test_excluded_checks_keep_real_results_without_blocking_pass(stage, status, reliable):
    record = receipt()
    record['recognition_config']['acceptance'][f'{stage}_match_mode'] = 'none'
    record[f'{stage}_check'].update(status=status, reliable=reliable)
    original = deepcopy(record[f'{stage}_check'])
    record['review_issues'] = [issue('evidence_unreliable', stage, '已识别的比对证据')]
    finalize_result(record)
    assert record['overall'] == '通过'
    assert record[f'{stage}_check'] == original


@pytest.mark.parametrize('stage', ['date', 'seal'])
def test_enabled_missing_evidence_still_blocks_pass(stage):
    record = receipt()
    record[f'{stage}_check'].update(status='未识别', reliable=False)
    assert finalize_result(record)['overall'] == '需人工复核'


def test_legacy_skipped_signature_is_recomputed_even_with_unchanged_source_fields():
    record = receipt()
    fields = record['fields']
    prior = {'contact': fields['仓库联系人'], 'receiver': fields['仓库接收人'],
             'status': '未核对', 'reliable': True, 'source': '按设置跳过', 'match_mode': 'none'}
    assert derive_signature_check(fields, prior)['status'] == '不匹配'


@pytest.mark.parametrize('contact', ['赵立新/张小虎', '赵建新/张小虎'])
def test_live_assembly_obeys_pass_criteria_and_preserves_provider_contacts(tmp_path, contact):
    results = {stage: [] for stage in STAGES}
    printed = {'仓库联系人': '赵立新、张小虎', '客户名称': '测试客户',
               '要求到货': '2026-09-20', '签章要求': '客户收货章'}
    results['fields'] = [
        {'method': 'paddle_v6', 'result': {'fields': printed}},
        {'method': 'danzhengtong', 'result': {'fields': {**printed, '仓库联系人': contact}}},
    ]
    results['handwriting'] = [{'method': 'danzhengtong', 'result': {
        'handwriting_fields': {'仓库接收人': '赵建新'}}}]
    source = receipt()
    for stage in ('date', 'seal'):
        results[stage] = [{'method': 'paddle_v6', 'result': {f'{stage}_check': source[f'{stage}_check']}}]
    config = {stage: [v['method'] for v in values] for stage, values in results.items()}
    config['acceptance'] = source['recognition_config']['acceptance']
    before = deepcopy(results)
    record = assemble_result(context(tmp_path), config, results, [], {}, {}, None, None, {})
    assert record['overall'] == '通过'
    assert record['signature_check']['status'] == ('不匹配' if contact.startswith('赵立新') else '匹配')
    assert record['printed_field_selection']['conflicts'] == []
    if not contact.startswith('赵立新'):
        assert record['fields']['仓库联系人'] == contact
        assert record['printed_field_selection']['fields']['仓库联系人']['selected_method'] == 'danzhengtong'
    assert results == before


@pytest.mark.parametrize('stage', ['date', 'seal'])
def test_startup_does_not_requeue_a_mismatch_excluded_from_the_criteria(tmp_path, stage):
    from receipt_ocr.persistence.database import Database

    database = Database(tmp_path / 'criteria.db')
    database.initialize()
    record = receipt()
    record['recognition_config']['acceptance'][f'{stage}_match_mode'] = 'none'
    record[f'{stage}_check'].update(status='不匹配', reliable=False)
    finalize_result(record)
    identity = database.insert_result(filename='receipt.jpg', stored_name='receipt.jpg',
        preview_name='', task_id='', result=record)
    assert database.enforce_uncertain_review_queue() == 0
    saved = database.get_result(identity)
    assert saved['final_result'] == '通过'
    assert saved[f'{stage}_check']['status'] == '不匹配'


def test_saved_rule_refresh_accepts_automatic_remote_history_but_retains_machine_baseline(tmp_path):
    import json
    from receipt_ocr.persistence.database import Database
    from receipt_ocr.provider_result_refresh import refresh_printed_fields_result
    from test_printed_field_refresh import saved_result
    from tools.analysis.operations.refresh_provider_results import refresh_database

    path = tmp_path / 'remote-result.db'
    database = Database(path)
    database.initialize()
    current = saved_result()
    identity = database.insert_result(filename='receipt.jpg', stored_name='receipt.jpg',
        preview_name='', task_id='', result=current)
    database.review_result(identity, result=current, review_status='待复核',
        final_result='需人工复核', note='', action='取回单证通结果')
    with database.connect() as connection:
        baseline = connection.execute('SELECT original_result_json FROM results WHERE id=?', (identity,)).fetchone()[0]
    assert refresh_database(path, ids=[identity], apply=True, refresh=refresh_printed_fields_result)['counts'] == {'changed': 1}
    assert database.get_result(identity)['final_result'] == '通过'
    assert database.get_result(identity)['seal_check'] == current['seal_check']
    with database.connect() as connection:
        assert connection.execute('SELECT original_result_json FROM results WHERE id=?', (identity,)).fetchone()[0] == baseline
    assert json.loads(database.history(identity)[0]['before_json'])['overall'] == '需人工复核'
