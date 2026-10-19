"""Unified listing must paginate/filter identities, not concatenate two lists."""
import pytest
from receipt_ocr.web import application as web
from receipt_ocr.persistence.database import Database
from receipt_ocr.jobs.store import JobStore

DAY = '2026-10-19'

@pytest.fixture
def managed(tmp_path, monkeypatch):
    db = Database(tmp_path / 'management.db'); db.initialize()
    store = JobStore(db); store.initialize()
    monkeypatch.setattr(web, 'database', db)
    monkeypatch.setattr(web, 'job_store', store)
    def batch(key, files, status='queued', result_id=None):
        task = store.create_batch(key, key, [{'filename': name} for name in files],
                                  {'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'local'})
        with db.connect() as con:
            con.execute('UPDATE batch_tasks SET created_at=? WHERE id=?', (DAY+'T09:30:00', key))
            con.execute('UPDATE recognition_jobs SET start_requested=1,created_at=?,status=?,result_id=? WHERE task_id=?',
                        (DAY+'T09:30:00', status, result_id, key))
        return task['items']
    def record(name='same.jpg', day=DAY, task=''):
        return db.insert_result(filename=name, stored_name=name, preview_name='', task_id=task, result={
            'created_at': day+'T09:00:00', 'fields': {'客户名称': '测试客户', '客户订单号': '7369737624'},
            'overall': '通过', 'review_status': '确认通过',
            'date_check': {'actual': DAY, 'status': '匹配', 'reliable': True},
            'seal_check': {'status': '匹配', 'reliable': True}})
    client = web.app.test_client()
    def get(**filters):
        response = client.get('/api/results', query_string={'include_processing': '1', 'import_date': DAY,
                              'page': 1, 'page_size': 25, **filters})
        assert response.status_code == 200, response.get_json()
        return response.get_json()
    return db, batch, record, get, client


def test_pending_files_participate_in_search_counts_and_pagination(managed):
    _, batch, record, get, _ = managed
    record('done.jpg')
    batch('a', ['same.jpg', 'same.jpg', 'other.jpg'])
    data = get(page_size=2)
    assert data['total'] == 4
    assert data['status_counts']['处理中'] == 3
    assert len(data['items']) == 2
    assert all(row['id'] is None for row in data['items'])
    assert get(page=2, page_size=2)['items'][-1]['filename'] == 'done.jpg'
    same = get(search='same')
    assert same['total'] == 2
    assert len({row['processing_job']['id'] for row in same['items']}) == 2
    assert get(overall='通过')['total'] == 1
    assert get(customer='测试客户')['total'] == 1


def test_retry_hides_saved_verdict_and_old_checks_until_finished(managed):
    db, batch, record, get, _ = managed
    rid = record()
    job = batch('retry', ['same.jpg'], status='running', result_id=rid)[0]
    data = get()
    assert data['total'] == 1
    assert data['items'][0]['id'] == rid
    assert data['items'][0]['final_result'] == '识别中'
    assert data['items'][0]['date_check'] == {'status': '待识别'}
    assert get(overall='通过')['total'] == 0
    assert get(seal_status='匹配')['total'] == 0
    assert get(overall='处理中')['total'] == 1
    with db.connect() as con:
        con.execute("UPDATE recognition_jobs SET status='succeeded' WHERE id=?", (job['id'],))
    assert get(overall='通过')['total'] == 1
    assert not get()['items'][0].get('processing_status')


def test_batch_scope_cross_day_retry_and_deleted_target(managed):
    db, batch, record, get, _ = managed
    old = record(day='2026-10-18')
    record('same.jpg')
    batch('retry', ['same.jpg'], status='running', result_id=old)
    assert get()['total'] == 2
    assert get(task_id='retry')['items'][0]['id'] == old
    with db.connect() as con:
        con.execute("UPDATE results SET deleted_at='removed' WHERE id=?", (old,))
    assert get(task_id='retry')['total'] == 0
    assert get()['total'] == 1


def test_failed_upload_is_not_a_rejected_receipt_and_legacy_review_stays_result_only(managed):
    _, batch, record, get, client = managed
    record()
    batch('failed', ['failed.jpg'], status='failed')
    assert get(overall='识别失败')['total'] == 1
    assert get(overall='不通过')['total'] == 0
    assert len(client.get('/api/results', query_string={'import_date': DAY}).get_json()) == 1
    assert client.get('/api/results?include_processing=1&import_date=bad').status_code == 400


def test_original_day_also_overlays_a_later_retry_and_queue_can_control_it(managed):
    db, batch, record, get, client = managed
    rid = record(day='2026-10-18')
    jobs = batch('new-day-retry', ['same.jpg'], status='running', result_id=rid)
    original = get(import_date='2026-10-18')
    assert original['total'] == 1
    assert original['items'][0]['processing_status'] == 'running'
    queue = client.get('/api/queue?import_date=2026-10-18&include_related=1').get_json()
    assert [job['id'] for job in queue] == [jobs[0]['id']]
    assert get(import_date='2026-10-18', overall='通过')['total'] == 0


def test_review_uses_same_retry_batch_scope_and_preserves_deferred_metadata(managed):
    db, batch, record, get, client = managed
    rid = record(day='2026-10-18')
    batch('review-retry', ['same.jpg'], status='succeeded', result_id=rid)
    with db.connect() as con:
        con.execute("UPDATE results SET review_status='待复核',overall='需人工复核',final_result='需人工复核' WHERE id=?", (rid,))
    reviewed = get(task_id='review-retry', reviewable='1', deferred_ids=str(rid))
    assert [row['id'] for row in reviewed['items']] == [rid]
    assert reviewed['deferred_in_scope_ids'] == [rid]
    assert get(task_id='review-retry', reviewable='1', ids='')['total'] == 0
    assert client.get('/api/results?include_processing=1&import_date=2026-10-19&ids=bad').status_code == 400
    batch('still-running', ['same.jpg'], status='running', result_id=rid)
    assert get(reviewable='1')['total'] == 0
