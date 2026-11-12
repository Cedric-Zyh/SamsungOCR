"""Reading saved signature checks must retain the selected acceptance policy."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.domain.decision import apply_signature_match_mode
from receipt_ocr.domain.documents.pagination import merge_paginated_results
from receipt_ocr.domain.fields.schema import derive_signature_check, project_fields
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from receipt_ocr.review import apply_human_edits
from sample_support import SampleCenter
from test_sample_center import samples


def policy_result(mode):
    fields = {'仓库联系人': '徐素婷', '仓库接收人': '付国成'}
    result = {
        'fields': fields,
        'signature_check': derive_signature_check(fields),
        'recognition_config': {'acceptance': {'signature_match_mode': mode}},
        'recognition_variants': {'fields': [
            {'method': 'paddle_v6', 'details': {'fields': {'仓库联系人': '付国成'}}},
            {'method': 'danzhengtong', 'details': {'fields': {'仓库联系人': '徐素婷'}}},
        ]},
        'date_check': {'status': '匹配', 'actual': '2026-01-31', 'reliable': True},
        'seal_check': {'status': '匹配', 'recognized': '客户章', 'reliable': True},
        'overall': '通过', 'review_status': '无需复核', 'review_issues': [],
    }
    apply_signature_match_mode(result)
    return result


@pytest.mark.parametrize('mode,status', [('none', '不匹配'), ('any', '匹配'), ('all', '需人工复核')])
def test_saved_signature_policy_survives_database_and_sample_read_paths(samples, mode, status):
    ctx, client = samples
    service = SampleCenter(ctx)
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    service.store.enqueue(['one.png'], {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
                                      'recognition_config': settings['recognition_config']}, settings)
    job = service.store.claim()
    result = policy_result(mode)
    expected = deepcopy(result['signature_check'])
    assert expected['status'] == status
    service.store.finish(job, result)
    result_id = service.store.get(job['id'])['result_id']
    assert service.database.get_result(result_id)['signature_check'] == expected
    assert service.database.list_results()[0]['signature_check'] == expected
    assert service.database.query_receipts()['items'][0]['signature_check'] == expected
    detail = client.get(f'/api/samples/results/{result_id}').get_json()
    assert detail['signature_check'] == expected
    assert detail['fields']['仓库联系人'] == '徐素婷'
    assert detail['fields']['仓库接收人'] == '付国成'
    listing = client.get('/api/samples').get_json()
    assert next(item for item in listing['items'] if item['filename'] == 'one.png')['signature'] == status
    with service.database.connect() as con:
        saved = json.loads(con.execute('SELECT result_json FROM results WHERE id=?', (result_id,)).fetchone()[0])
    assert saved['signature_check'] == expected


@pytest.mark.parametrize('mode', ['none', 'any', 'all'])
def test_field_and_page_projection_retain_an_unchanged_policy_check(mode):
    result = policy_result(mode)
    expected = deepcopy(result['signature_check'])
    assert project_fields(deepcopy(result))['signature_check'] == expected
    cover = {**deepcopy(result), 'id': 1, 'filename': 'receipt.jpg', 'task_id': 'batch',
             'document_type': {'type': 'receipt'}}
    continuation = {'id': 2, 'filename': 'receipt_01.jpg', 'task_id': 'batch',
                    'document_type': {'type': 'product_continuation'}, 'fields': {}}
    merged = merge_paginated_results([cover, continuation])
    assert merged[0]['signature_check'] == expected


@pytest.mark.parametrize('mode', ['none', 'any', 'all'])
def test_editing_signature_source_fields_recomputes_the_comparison(mode):
    result = policy_result(mode)
    unchanged = apply_human_edits(result, {'fields': {'客户名称': '改客户名称'}})
    assert unchanged['signature_check'] == result['signature_check']
    changed = apply_human_edits(result, {'fields': {'仓库接收人': '徐素婷'}})
    assert changed['signature_check'] == {
        'contact': '徐素婷', 'receiver': '徐素婷', 'status': '匹配', 'reliable': True, 'source': '字段比对',
    }


def test_human_signature_choice_stays_explicit_until_a_source_field_changes():
    result = policy_result('none')
    confirmed = apply_human_edits(result, {'signature_confirmed_match': True})
    assert confirmed['signature_check']['source'] == '人工复核'
    assert confirmed['signature_check']['human_confirmed_match'] is True
    assert project_fields(deepcopy(confirmed))['signature_check'] == confirmed['signature_check']
    changed = apply_human_edits(confirmed, {'fields': {'仓库接收人': '其他人'}})
    assert changed['signature_check']['source'] == '字段比对'
    assert changed['signature_check']['status'] == '不匹配'
    assert 'human_confirmed_match' not in changed['signature_check']


def test_legacy_check_without_source_field_identity_is_recomputed():
    prior = {'status': '未核对', 'reliable': True, 'match_mode': 'none', 'source': '按设置跳过'}
    check = derive_signature_check({'仓库联系人': '甲', '仓库接收人': '乙'}, prior)
    assert check['status'] == '不匹配'
    assert check['source'] == '字段比对'
