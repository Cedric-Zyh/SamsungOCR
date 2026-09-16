"""Review scope filters apply to complete receipts before server pagination."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

import app as web
from receipt_ocr.database import Database
from receipt_ocr.job_store import JobStore


@pytest.fixture
def scoped_database(tmp_path, monkeypatch):
    database = Database(tmp_path / 'scope.db')
    database.initialize()
    monkeypatch.setattr(web, 'database', database)
    return database


def add_receipt(database, filename, *, customer='甲客户', status='待复核', task_id='', **extra):
    result = {'filename': filename, 'fields': {'客户名称': customer}, 'review_status': status,
              'overall': '需人工复核', **extra}
    return database.insert_result(filename=filename, stored_name='', preview_name='',
                                  task_id=task_id, result=deepcopy(result))


def test_selected_scope_filters_before_pagination_and_retains_customer(scoped_database):
    db = scoped_database
    selected = [add_receipt(db, f'selected-{index}.jpg') for index in range(105)]
    excluded = add_receipt(db, 'other-customer.jpg', customer='乙客户')
    add_receipt(db, 'unselected.jpg')
    client = web.app.test_client()
    query = {'ids': ','.join(map(str, [*selected, excluded])), 'customer': '甲客户',
             'reviewable': '1', 'page_size': '100'}
    pages = [client.get('/api/results', query_string={**query, 'page': number}).get_json()
             for number in (1, 2)]
    assert [len(page['items']) for page in pages] == [100, 5]
    assert all(page['total'] == 105 for page in pages)
    assert {row['id'] for page in pages for row in page['items']} == set(selected)


def test_empty_selected_scope_never_becomes_all_receipts(scoped_database):
    add_receipt(scoped_database, 'unselected.jpg')
    response = web.app.test_client().get('/api/results?ids=&page=1&reviewable=1').get_json()
    assert response['items'] == []
    assert response['total'] == 0


@pytest.mark.parametrize('query', ['ids=0', 'ids=-1', 'ids=1,bad', 'ids=1,,2', 'ids=01',
                                  'ids=1.5', 'ids=1&ids=2', 'ids=99999999999999999999', 'ids=１'])
def test_invalid_selected_scope_returns_error(scoped_database, query):
    add_receipt(scoped_database, 'unselected.jpg')
    response = web.app.test_client().get('/api/results?' + query)
    assert response.status_code == 400


def test_selected_continuation_preserves_complete_receipt_and_busy_protection(scoped_database):
    db = scoped_database
    cover = add_receipt(db, 'order.jpg', task_id='pages', document_type={'type':'receipt'},
                        product_table={'columns':['行号'], 'rows':[{'values':{'行号':'10'}}]})
    continuation = add_receipt(db, 'order_01.jpg', task_id='pages',
        document_type={'type':'product_continuation'}, fields={},
        product_table={'columns':['行号'], 'rows':[{'values':{'行号':'20'}}]})
    client = web.app.test_client()
    query = f'/api/results?ids={continuation},{cover},{cover}&page=1&reviewable=1'
    result = client.get(query).get_json()
    assert result['total'] == 1
    assert result['items'][0]['id'] == cover
    assert [row['values']['行号'] for row in result['items'][0]['product_table']['rows']] == ['10', '20']
    store = JobStore(db)
    store.initialize()
    store.create_retries('retry', [continuation], {continuation:{'ocr_backend':'vision'}})
    assert client.get(query).get_json()['total'] == 0


def test_selected_scope_does_not_include_confirmed_or_failed_receipts(scoped_database):
    db = scoped_database
    pending = add_receipt(db, 'pending.jpg')
    confirmed = add_receipt(db, 'confirmed.jpg', status='确认通过')
    failed = add_receipt(db, 'failed.jpg', overall='识别失败', error_message='识别失败')
    result = web.app.test_client().get(f'/api/results?ids={pending},{confirmed},{failed}&reviewable=1&page=1').get_json()
    assert [row['id'] for row in result['items']] == [pending]


def test_deferred_metadata_excludes_receipts_moved_out_of_date_filter_after_save(scoped_database):
    db = scoped_database
    ids = [add_receipt(db, f'date-{index}.jpg',
                      fields={'客户名称':'甲客户', '要求到货':'2026-09-10'},
                      date_check={'required':'2026-09-10', 'actual':'2026-09-10',
                                  'status':'匹配', 'reliable':False}) for index in range(3)]
    client = web.app.test_client()
    query = {'date_status':'匹配待确认', 'reviewable':'1', 'page':'1', 'page_size':'1',
             'deferred_ids':','.join(map(str, ids[:2]))}
    before = client.get('/api/results', query_string=query).get_json()
    assert before['total'] == 3
    assert set(before['deferred_in_scope_ids']) == set(ids[:2])
    # The deferred IDs are beyond the visible first page, but still counted.
    assert before['items'][0]['id'] == ids[2]
    response = client.patch(f'/api/results/{ids[0]}/review', json={
        'actual_date':'2026-09-11', 'review_status':'待复核', 'human_note':'稍后核对'})
    assert response.status_code == 200
    after = client.get('/api/results', query_string=query).get_json()
    assert after['total'] == 2
    assert after['deferred_in_scope_ids'] == [ids[1]]
    assert after['total'] - len(after['deferred_in_scope_ids']) == 1


@pytest.mark.parametrize('daily', [False, True])
def test_deferred_metadata_drops_receipts_reviewed_elsewhere(scoped_database, daily):
    db = scoped_database
    ids = [add_receipt(db, f'pending-{index}.jpg') for index in range(3)]
    day = db.get_result(ids[0])['created_at'][:10]
    client = web.app.test_client()
    url = '/api/daily-results' if daily else '/api/results'
    query = {'import_date':day, 'reviewable':'1', 'page':'1', 'page_size':'1',
             'deferred_ids':','.join(map(str, ids[:2]))}
    before = client.get(url, query_string=query).get_json()
    assert before['total'] == 3
    assert set(before['deferred_in_scope_ids']) == set(ids[:2])
    response = client.patch(f'/api/results/{ids[0]}/review', json={
        'review_status':'确认不通过', 'final_result':'不通过'})
    assert response.status_code == 200
    after = client.get(url, query_string=query).get_json()
    assert after['total'] == 2
    assert after['deferred_in_scope_ids'] == [ids[1]]


def test_selected_scope_metadata_does_not_count_deferred_items_outside_selection(scoped_database):
    db = scoped_database
    first, second = [add_receipt(db, f'{index}.jpg') for index in range(2)]
    result = web.app.test_client().get('/api/results', query_string={
        'ids':str(first), 'deferred_ids':f'{first},{second}', 'page':'1', 'reviewable':'1'}).get_json()
    assert result['total'] == 1
    assert result['deferred_in_scope_ids'] == [first]


def test_frontend_review_scopes_and_session_progress():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    result = subprocess.run([node, '--test', 'tests/frontend_review_scope.cjs'],
                            cwd=Path(__file__).resolve().parents[1], text=True,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
