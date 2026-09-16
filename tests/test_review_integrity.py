from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import app as web
from receipt_ocr.database import Database
from receipt_ocr.job_store import JobStore
from receipt_ocr.review import apply_human_edits


def uncertain_receipt():
    return {
        'filename': 'receipt.jpg', 'field_schema_version': 1,
        'fields': {'要求到货': '2025-05-11', '签章要求': '测试科技有限公司', '客户名称': '测试客户'},
        'field_metadata': {'客户名称': {'value': '测试客户', 'confidence': .4, 'low_confidence': True}},
        'date_check': {'actual': '2025-05-11', 'status': '匹配', 'confidence': .35, 'reliable': False},
        'seal_check': {'recognized': '测试科技有限公司', 'status': '匹配', 'score': .9, 'confidence': .6, 'reliable': False},
        'overall': '需人工复核', 'final_result': '需人工复核', 'review_status': '待复核',
        'review_reasons': ['日期证据不足', '存在低置信度字段'],
    }


@pytest.fixture
def database(tmp_path, monkeypatch):
    db = Database(tmp_path / 'review.db')
    db.initialize()
    monkeypatch.setattr(web, 'database', db)
    monkeypatch.setattr(web, 'GROUND_TRUTH_PATH', tmp_path / 'truth.json')
    return db


def insert(db, result, *, task_id=''):
    return db.insert_result(filename=result['filename'], stored_name=result['filename'],
                            preview_name='', task_id=task_id, result=deepcopy(result))


@pytest.mark.parametrize('roundtrip_fields', [False, True])
def test_note_save_preserves_uncertain_evidence_and_original(database, roundtrip_fields):
    original = uncertain_receipt()
    result_id = insert(database, original)
    payload = {'human_note': '稍后核对', 'review_status': '待复核'}
    if roundtrip_fields:
        payload.update(fields=original['fields'], actual_date=original['date_check']['actual'],
                       seal_text=original['seal_check']['recognized'], actual_date_confirmed=False,
                       seal_confirmed_match=None, final_result='')
    response = web.app.test_client().patch(f'/api/results/{result_id}/review', json=payload)
    assert response.status_code == 200
    saved = response.get_json()
    for key in ('date_check', 'seal_check', 'overall', 'review_reasons'):
        assert saved[key] == original[key]
    assert saved['final_result'] == '需人工复核'
    assert saved['human_note'] == '稍后核对'
    assert database.list_original_results()[0]['date_check']['reliable'] is False


def test_confirming_overall_does_not_imply_confirmation_of_date_or_seal(database):
    result = uncertain_receipt()
    result_id = insert(database, result)
    response = web.app.test_client().patch(f'/api/results/{result_id}/review', json={
        'fields': result['fields'], 'actual_date': result['date_check']['actual'],
        'seal_text': result['seal_check']['recognized'], 'review_status': '确认通过', 'final_result': '通过',
    })
    assert response.status_code == 409
    assert database.history(result_id) == []


def test_explicit_unchanged_evidence_can_be_confirmed(database):
    result = uncertain_receipt()
    result_id = insert(database, result)
    response = web.app.test_client().patch(f'/api/results/{result_id}/review', json={
        'fields': result['fields'], 'actual_date': result['date_check']['actual'], 'actual_date_confirmed': True,
        'seal_text': result['seal_check']['recognized'], 'seal_confirmed_match': True,
        'review_status': '确认通过', 'final_result': '通过',
    })
    assert response.status_code == 200
    saved = response.get_json()
    assert saved['overall'] == '通过'
    assert saved['date_check']['source'] == '人工复核'
    assert saved['seal_check']['human_confirmed_match'] is True
    assert saved['field_metadata']['客户名称']['low_confidence'] is False
    assert database.list_original_results()[0]['seal_check']['reliable'] is False


def test_editing_requirement_does_not_certify_unchanged_machine_text():
    current = uncertain_receipt()
    saved = apply_human_edits(current, {'fields': {'要求到货': '2025-06-11', '签章要求': '另一家公司'}})
    assert saved['date_check']['status'] == '不匹配'
    assert saved['date_check']['reliable'] is False
    assert saved['seal_check']['reliable'] is False
    assert saved['overall'] == '需人工复核'
    assert current == uncertain_receipt()


def test_invalid_date_confirmation_does_not_write(database):
    result_id = insert(database, uncertain_receipt())
    response = web.app.test_client().patch(f'/api/results/{result_id}/review', json={'actual_date_confirmed': 'true'})
    assert response.status_code == 400
    assert database.history(result_id) == []


def paginated_pair(db):
    ids = []
    for filename, kind, number in [('order.jpg', 'receipt', '10'), ('order_01.jpg', 'product_continuation', '40')]:
        result = uncertain_receipt()
        result.update(filename=filename, document_type={'type': kind, 'label': kind},
                      product_table={'columns': ['行号'], 'rows': [{'values': {'行号': number},
                                     'confidences': {'行号': .95}}], 'confidence': .95})
        if kind == 'product_continuation':
            result['fields'] = {}
        else:
            result['internal_fields'] = {'客户订单号': 'ORDER-123'}
        ids.append(insert(db, result, task_id='paged'))
    return ids


def test_bulk_review_never_persists_merged_rows_as_physical_page(database):
    cover, continuation = paginated_pair(database)
    client = web.app.test_client()
    for _ in range(2):
        response = client.post('/api/results/bulk-review', json={'ids': [cover], 'review_status': '待复核'})
        assert response.status_code == 200
        detail = client.get(f'/api/results/{cover}').get_json()
        assert [row['values']['行号'] for row in detail['product_table']['rows']] == ['10', '40']
    assert len(database.get_result(cover)['product_table']['rows']) == 1
    assert len(database.get_result(continuation)['product_table']['rows']) == 1


def test_bulk_review_rollback_on_later_failure(database, monkeypatch):
    ids = [insert(database, {**uncertain_receipt(), 'filename': f'{i}.jpg'}) for i in range(2)]
    original = database.review_result
    def fail_second(result_id, **kwargs):
        if result_id == ids[1]:
            raise ValueError('保存失败')
        return original(result_id, **kwargs)
    monkeypatch.setattr(database, 'review_result', fail_second)
    response = web.app.test_client().post('/api/results/bulk-review', json={
        'ids': ids, 'review_status': '确认不通过', 'final_result': '不通过',
    })
    assert response.status_code == 400
    assert all(database.get_result(i)['review_status'] == '待复核' for i in ids)
    assert all(database.history(i) == [] for i in ids)


def test_filter_and_export_include_all_associated_pages(database, monkeypatch, tmp_path):
    paginated_pair(database)
    client = web.app.test_client()
    rows = client.get('/api/results?order_id=ORDER-123&page=1&page_size=1').get_json()
    assert rows['total'] == 1
    assert len(rows['items'][0]['product_table']['rows']) == 2
    captured = []
    def export(command, **kwargs):
        from pathlib import Path
        captured.append(json.loads(Path(command[-2]).read_text()))
        Path(command[-1]).write_bytes(b'test-workbook')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(web, 'EXPORT_DIR', tmp_path)
    monkeypatch.setattr(web, 'NODE_EXECUTABLE', 'node')
    monkeypatch.setattr(web.subprocess, 'run', export)
    response = client.get('/api/export.xlsx?order_id=ORDER-123')
    assert response.status_code == 200
    assert len(captured[0]['results'][0]['product_table']['rows']) == 2


def test_reviewable_pagination_excludes_failures_and_busy_associated_pages(database):
    cover, continuation = paginated_pair(database)
    pending = insert(database, {**uncertain_receipt(), 'filename': 'pending.jpg'})
    insert(database, {**uncertain_receipt(), 'filename': 'failed.jpg', 'overall': '识别失败'})
    store = JobStore(database)
    store.initialize()
    store.create_retries('retry', [continuation], {continuation: {'ocr_backend': 'vision'}})
    client = web.app.test_client()
    for url in ['/api/results', '/api/history', '/api/daily-results?import_date=' + database.get_result(pending)['created_at'][:10]]:
        response = client.get(url + ('&' if '?' in url else '?') + 'page=1&page_size=1&reviewable=1')
        assert response.status_code == 200
        payload = response.get_json()
        assert payload['total'] == 1
        assert payload['items'][0]['id'] == pending


def test_explicit_pagination_reaches_records_beyond_old_limit(database):
    with database.connect() as connection:
        for index in range(1005):
            database.insert_result(filename=f'{index}.jpg', stored_name='', preview_name='', task_id='',
                                   result={'overall': '需人工复核'}, _connection=connection)
    client = web.app.test_client()
    pages = [client.get(f'/api/results?page={page}&page_size=500').get_json() for page in (1, 2, 3)]
    assert [len(page['items']) for page in pages] == [500, 500, 5]
    assert all(page['total'] == 1005 for page in pages)
    assert len({row['id'] for page in pages for row in page['items']}) == 1005
    for query in ('page=0', 'page=bad', 'page_size=2001', 'page_size=0'):
        assert client.get('/api/results?' + query).status_code == 400


def test_stale_note_form_cannot_overwrite_retry_or_promote_old_evidence(database):
    result_id = insert(database, uncertain_receipt())
    client = web.app.test_client()
    old = client.get(f'/api/results/{result_id}').get_json()
    new = uncertain_receipt()
    new['date_check']['actual'] = '2025-06-11'
    new['seal_check']['recognized'] = '另一家公司'
    database.replace_after_retry(result_id, new, '')
    response = client.patch(f'/api/results/{result_id}/review', json={
        'fields': old['fields'], 'actual_date': old['date_check']['actual'], 'actual_date_confirmed': False,
        'seal_text': old['seal_check']['recognized'], 'seal_confirmed_match': None,
        'human_note': '只修改备注', 'review_status': '待复核', 'review_revision': old['review_revision'],
    })
    assert response.status_code == 409
    assert response.get_json()['code'] == 'review_revision_conflict'
    saved = database.get_result(result_id)
    assert saved['date_check']['actual'] == '2025-06-11'
    assert saved['date_check']['reliable'] is False
    assert saved['seal_check']['recognized'] == '另一家公司'
    assert len(database.history(result_id)) == 1


def test_successful_save_returns_revision_for_subsequent_edit(database):
    result_id = insert(database, uncertain_receipt())
    client = web.app.test_client()
    revision = client.get(f'/api/results/{result_id}').get_json()['review_revision']
    for note in ('第一次备注', '第二次备注'):
        response = client.patch(f'/api/results/{result_id}/review', json={'human_note': note, 'review_revision': revision})
        assert response.status_code == 200
        new_revision = response.get_json()['review_revision']
        assert new_revision != revision
        revision = new_revision
    assert len(database.history(result_id)) == 2


def test_page_override_does_not_hide_changed_source_from_review_revision(database):
    cover, continuation = paginated_pair(database)
    client = web.app.test_client()
    assert client.post('/api/results/bulk-review', json={'ids': [cover], 'review_status': '待复核'}).status_code == 200
    old = client.get(f'/api/results/{cover}').get_json()
    fresh = database.get_result(continuation)
    fresh['product_table']['rows'][0]['values']['行号'] = '50'
    database.replace_after_retry(continuation, fresh, '')
    response = client.patch(f'/api/results/{cover}/review', json={'human_note': '旧页面备注', 'review_revision': old['review_revision']})
    assert response.status_code == 409
    assert response.get_json()['code'] == 'review_revision_conflict'
