from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
import subprocess
import sys
from types import SimpleNamespace

import pytest

from receipt_ocr.database import Database, now_iso
from receipt_ocr.job_store import JobStore, result_revision
from receipt_ocr.job_worker import JobWorker, ProcessLock
from receipt_ocr.job_service import ReceiptJobService


OPTIONS = dict(ocr_backend='vision', seal_recognition_mode='local', recognition_config=None)


def result():
    return {'overall': '通过', 'final_result': '通过', 'review_status': '无需复核',
            'fields': {'客户名称': '机器客户'},
            'date_check': {'actual': '2026-09-09', 'status': '匹配', 'reliable': True},
            'seal_check': {'recognized': '客户章', 'status': '匹配', 'reliable': True}}


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / 'results.db')
    database.initialize()
    store = JobStore(database)
    store.initialize()
    return store


def batch(store, total=1, task_id='task'):
    return store.create_batch(task_id, '测试批次', [{'filename': f'{i}.jpg'} for i in range(total)], OPTIONS)


def queued(store, task_id='task'):
    task = batch(store, task_id=task_id)
    job, accepted = store.accept_upload(task['items'][0]['id'], 'saved.jpg')
    assert accepted
    store.start_jobs([job['id']])
    return store.get(job['id'])


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('background condition did not complete')


def test_upload_is_idempotent_and_unuploaded_slots_are_explicit(store):
    task = batch(store, total=2)
    job_id = task['items'][0]['id']
    assert store.task('task')['awaiting_upload'] == 2
    store.accept_upload(job_id, 'first.jpg')
    duplicate, accepted = store.accept_upload(job_id, 'second.jpg')
    assert not accepted and duplicate['stored_name'] == 'first.jpg'
    assert store.public(duplicate)['status'] == 'ready'
    assert store.claim() is None
    store.start_jobs([job_id])
    job = store.claim()
    assert job['id'] == job_id
    store.finish(job, result(), 'preview.jpg')
    task = store.task('task')
    assert task['completed'] == 1 and task['awaiting_upload'] == 1
    assert task['status'] == '等待上传'
    assert len(store.database.list_results()) == 1


def test_process_history_tracks_queue_lifecycle(store):
    task = batch(store)
    job_id = task['items'][0]['id']
    store.accept_upload(job_id, 'saved.jpg')
    store.start_jobs([job_id])
    job = store.claim()
    assert store.update_progress(job, {
        'provider': 'danzhengtong',
        'stage': 'waiting',
        'updated_at': now_iso(),
        'poll_count': 1,
    })
    store.finish(job, result(), 'preview.jpg')

    history = store.process_history(job_id=job_id)
    titles = [event['title'] for event in history['events']]
    assert titles[:4] == ['已提交文件', '文件已上传', '已加入识别队列', '开始识别']
    assert '等待识别结果' in titles
    assert titles[-1] == '处理完成'

    result_id = store.get(job_id)['result_id']
    by_result = store.process_history(result_id=result_id)
    assert by_result['job_id'] == job_id
    assert [event['title'] for event in by_result['events']] == titles


def test_delete_job_removes_unfinished_item_and_prevents_claim(store):
    task = batch(store, total=2)
    first, second = [item['id'] for item in task['items']]
    store.accept_upload(first, 'first.jpg')
    deleted, changed = store.delete_job(first)
    assert changed is True
    assert deleted['id'] == first
    assert store.claim() is None
    assert all(item['id'] != first for item in store.task(task['id'])['items'])
    assert store.task(task['id'])['total'] == 1

    store.accept_upload(second, 'second.jpg')
    store.start_jobs([second])
    running = store.claim()
    deleted, changed = store.delete_job(second)
    assert changed is True
    assert store.finish(running, result()) is False
    assert store.database.list_results() == []


def test_delete_jobs_removes_all_unfinished_items_atomically(store):
    task = batch(store, total=3)
    ids = [item['id'] for item in task['items']]
    store.accept_upload(ids[0], 'first.jpg')
    store.accept_upload(ids[1], 'second.jpg')
    result = store.delete_jobs(ids)
    assert {item['id'] for item in result['deleted']} == set(ids)
    assert result['kept'] == []
    assert store.task(task['id'])['items'] == []
    assert store.task(task['id'])['total'] == 0


def test_import_waits_for_explicit_start_after_reload_recovery_and_resume(store):
    task = batch(store, total=2)
    assert all(item['start_requested'] is False for item in task['items'])
    for item in task['items']:
        store.accept_upload(item['id'], item['filename'])
    assert store.task('task')['status'] == '待开始'
    assert store.task('task')['ready'] == store.control()['ready'] == 2
    assert store.control()['queued'] == 0
    assert store.task('task')['completed'] == 0
    assert store.claim() is None

    restarted = JobStore(Database(store.database.path))
    restarted.initialize()
    restarted.set_paused(True)
    restarted.recover()
    restarted.set_paused(False)
    assert restarted.claim() is None
    assert [job['status'] for job in restarted.daily(task['created_at'][:10])] == ['ready', 'ready']
    # Even an older consumer that tries to bypass claim() cannot run a held job.
    with pytest.raises(sqlite3.IntegrityError, match='recognition_start_required'):
        with restarted.database.connect() as con:
            con.execute("UPDATE recognition_jobs SET status='running' WHERE id=?", (task['items'][0]['id'],))

    selected = task['items'][0]['id']
    assert restarted.start_jobs([selected])['started'] == 1
    assert restarted.start_jobs([selected, selected])['started'] == 0
    job = restarted.claim()
    assert job['id'] == selected
    assert restarted.claim() is None
    assert restarted.finish(job, result())
    assert restarted.start_jobs([selected])['started'] == 0
    assert restarted.claim() is None
    assert restarted.control()['ready'] == 1


def test_start_is_atomic_and_does_not_authorize_unuploaded_or_other_jobs(store):
    task = batch(store, total=3)
    first, second, missing_upload = [item['id'] for item in task['items']]
    store.accept_upload(first, 'first.jpg')
    store.accept_upload(second, 'second.jpg')
    with pytest.raises(KeyError):
        store.start_jobs([first, 'unknown'])
    with pytest.raises(ValueError, match='尚未导入完成'):
        store.start_jobs([first, missing_upload])
    assert store.get(first)['start_requested'] == 0
    assert store.claim() is None
    store.set_paused(True)
    assert store.start_jobs([first])['started'] == 1
    assert store.claim() is None
    assert store.get(second)['start_requested'] == 0
    assert store.get(missing_upload)['start_requested'] == 0
    store.set_paused(False)
    assert store.claim()['id'] == first
    assert store.claim() is None


def test_existing_queue_migration_keeps_already_authorized_jobs_running(store):
    job = queued(store)
    with store.database.connect() as con:
        con.execute('DROP TRIGGER require_recognition_start')
        con.execute('DROP INDEX idx_jobs_start')
        con.execute('ALTER TABLE recognition_jobs DROP COLUMN start_requested')
    restarted = JobStore(Database(store.database.path))
    restarted.initialize()
    assert restarted.public(restarted.get(job['id']))['start_requested'] is True
    assert restarted.claim()['id'] == job['id']
    imported = batch(restarted, task_id='new-task')
    assert imported['items'][0]['start_requested'] is False


def test_restart_recovers_queue_without_interrupting_it_as_legacy(store):
    queued(store)
    first_claim = store.claim()
    store.database.create_task('legacy', '旧同步任务', 2)
    assert store.database.recover_interrupted_tasks() == 1
    assert store.database.get_task('legacy')['status'] == '已中断'
    assert store.task('task')['running'] == 1
    restarted = JobStore(Database(store.database.path))
    restarted.recover()
    new_claim = restarted.claim()
    assert new_claim['id'] == first_claim['id']
    assert new_claim['attempt'] == 2
    assert not restarted.finish(first_claim, result())
    assert restarted.finish(new_claim, result())
    assert not restarted.finish(new_claim, result())
    assert len(store.database.list_results()) == 1
    assert store.task('task')['completed'] == 1


def test_result_and_completion_rollback_together(store, monkeypatch):
    queued(store)
    job = store.claim()
    reconcile = store.database.reconcile_task_pages
    def failing(*args, **kw):
        reconcile(*args, **kw)
        raise RuntimeError('disk failed while saving page relationships')
    monkeypatch.setattr(store.database, 'reconcile_task_pages', failing)
    with pytest.raises(RuntimeError):
        store.finish(job, result())
    assert store.database.list_results() == []
    assert store.get(job['id'])['status'] == 'running'
    monkeypatch.setattr(store.database, 'reconcile_task_pages', reconcile)
    store.finish(job, result())
    assert len(store.database.list_results()) == 1


def test_failure_retries_only_failed_item_and_preserves_first_machine_output(store):
    task = batch(store, 2)
    for item in task['items']:
        store.accept_upload(item['id'], item['filename'])
    store.start_jobs([item['id'] for item in task['items']])
    first, second = store.claim(), store.claim()
    store.finish(first, error='OCR timed out')
    store.finish(second, result())
    assert store.task('task')['failed'] == 1
    store.retry_failed(first['id'])
    store.retry_failed(first['id'])  # double-click must not create a second run
    retry = store.claim()
    assert retry['id'] == first['id'] and store.claim() is None
    store.finish(retry, result())
    assert len(store.database.list_results()) == 2
    assert store.task('task')['succeeded'] == 2
    record_id = store.get(first['id'])['result_id']
    assert store.database.get_result(record_id)['attempt'] == 2
    original = next(r for r in store.database.list_original_results() if r['id'] == record_id)
    assert original['overall'] == '识别失败'
    assert len(store.database.history(record_id)) == 1


def prepare_retry(store):
    queued(store)
    first = store.claim()
    store.finish(first, result())
    record_id = store.get(first['id'])['result_id']
    store.create_retries('retry', [record_id], {record_id: OPTIONS})
    return record_id, store.claim()


def test_new_review_during_background_retry_is_not_overwritten(store):
    record_id, job = prepare_retry(store)
    human = store.database.get_result(record_id)
    human['fields']['客户名称'] = '人工修正客户'
    store.database.review_result(record_id, result=human, review_status='确认通过',
                                 final_result='通过', note='人工已确认', action='人工复核')
    store.finish(job, result())
    assert store.get(job['id'])['status'] == 'failed'
    assert '已保留最新复核结果' in store.get(job['id'])['error_message']
    assert store.database.get_result(record_id)['fields']['客户名称'] == '人工修正客户'
    assert store.database.get_result(record_id)['attempt'] == 1


def test_deleted_result_is_not_recreated_by_retry(store):
    record_id, job = prepare_retry(store)
    store.database.delete_results([record_id])
    store.finish(job, result())
    assert store.get(job['id'])['status'] == 'cancelled'
    assert store.database.list_results() == []


def test_retry_failure_leaves_existing_result_intact(store):
    record_id, job = prepare_retry(store)
    before = deepcopy(store.database.get_result(record_id))
    store.finish(job, error='remote unavailable')
    assert store.database.get_result(record_id) == before
    assert store.get(job['id'])['status'] == 'failed'


def test_retry_enqueuing_rejects_duplicates_and_is_atomic(store):
    record_id, job = prepare_retry(store)
    with pytest.raises(ValueError):
        store.create_retries('duplicate', [record_id], {record_id: OPTIONS})
    with pytest.raises(KeyError):
        store.database.get_task('duplicate')
    store.finish(job, result())
    with pytest.raises(KeyError):
        store.create_retries('bad', [record_id, 99999], {record_id: OPTIONS})
    assert store.claim() is None


def test_single_worker_ownership_and_takeover(store):
    first_lock = ProcessLock(store.database.path.with_suffix('.worker.lock'))
    second_lock = ProcessLock(first_lock.path)
    try:
        assert first_lock.acquire()
        assert not second_lock.acquire()
        first_lock.release()
        assert second_lock.acquire()
    finally:
        first_lock.release(); second_lock.release()


def test_two_workers_do_not_duplicate_jobs(store):
    task = batch(store, 3)
    for item in task['items']:
        store.accept_upload(item['id'], item['filename'])
    store.start_jobs([item['id'] for item in task['items']])
    seen = []
    def execute(job):
        seen.append(job['id'])
        return result(), ''
    workers = [JobWorker(store, execute), JobWorker(store, execute)]
    try:
        for worker in workers:
            worker.start()
        wait_until(lambda: store.task('task')['completed'] == 3)
    finally:
        for worker in workers:
            worker.stop()
    assert len(seen) == len(set(seen)) == 3
    assert len(store.database.list_results()) == 3


def test_process_crash_releases_ownership_and_next_worker_recovers(store):
    job = queued(store)
    script = '''
import os, sys, threading
from receipt_ocr.database import Database
from receipt_ocr.job_store import JobStore
from receipt_ocr.job_worker import JobWorker
store = JobStore(Database(sys.argv[1]))
def crash(job):
    os._exit(0)
JobWorker(store, crash).start()
threading.Event().wait(8)
raise RuntimeError('worker did not start')
'''
    process = subprocess.run([sys.executable, '-c', script, str(store.database.path)],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
    assert store.get(job['id'])['status'] == 'running'
    worker = JobWorker(store, lambda job: (result(), ''))
    try:
        worker.start()
        wait_until(lambda: store.get(job['id'])['status'] == 'succeeded')
    finally:
        worker.stop()
    assert store.get(job['id'])['attempt'] == 2
    assert len(store.database.list_results()) == 1


def test_retry_commit_failure_rolls_back_history_and_result(store, monkeypatch):
    record_id, job = prepare_retry(store)
    before = store.database.get_result(record_id)
    replace = store.database.replace_after_retry
    def failing(*args, **kwargs):
        replace(*args, **kwargs)
        raise RuntimeError('commit interrupted')
    monkeypatch.setattr(store.database, 'replace_after_retry', failing)
    with pytest.raises(RuntimeError):
        store.finish(job, result())
    assert store.database.get_result(record_id) == before
    assert store.database.history(record_id) == []
    assert store.get(job['id'])['status'] == 'running'


def test_deleted_jobs_are_not_resurrected_in_daily_progress(store):
    job = queued(store)
    store.finish(store.claim(), result())
    store.database.delete_results([store.get(job['id'])['result_id']])
    assert store.daily(now_iso()[:10]) == []


@pytest.fixture
def web_queue(store, tmp_path, monkeypatch):
    import app as web
    for name, child in [('UPLOAD_DIR', 'uploads'), ('PREVIEW_DIR', 'previews'),
                         ('ARTIFACT_DIR', 'artifacts'), ('DATA_DIR', 'data')]:
        directory = tmp_path / child
        directory.mkdir()
        monkeypatch.setattr(web, name, directory)
    monkeypatch.setattr(web, 'database', store.database)
    monkeypatch.setattr(web, 'job_store', store)
    monkeypatch.setattr(web, 'job_worker', None)
    monkeypatch.setattr(web, 'resolve_backend', lambda name: 'vision')
    monkeypatch.setattr(web, 'analyzer', SimpleNamespace(seal_api=SimpleNamespace(enabled=False)))
    return web


def test_http_upload_waits_for_start_and_finishes_without_browser(web_queue, monkeypatch):
    web = web_queue
    started, release = threading.Event(), threading.Event()
    def analyze(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return result()
    service = ReceiptJobService(analyze, data_dir=web.DATA_DIR,
        upload_dir=web.UPLOAD_DIR, preview_dir=web.PREVIEW_DIR, artifact_dir=web.ARTIFACT_DIR)
    worker = JobWorker(web.job_store, service)
    monkeypatch.setattr(web, 'job_worker', worker)
    try:
        worker.start()
        with web.app.test_client() as client:
            task = client.post('/api/tasks', json={'background': True, 'total': 1,
                'items': [{'filename': 'receipt.jpg'}], 'ocr_backend': 'vision'}).get_json()
            job_id = task['items'][0]['id']
            response = client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b'image'), 'receipt.jpg')})
            assert response.status_code == 202
            assert response.get_json()['status'] == 'ready'
            assert response.get_json()['start_requested'] is False
            assert not started.wait(timeout=.15)
            assert web.job_store.claim() is None
            assert client.post('/api/jobs/start', json={'ids': [job_id]}).status_code == 202
            assert started.wait(timeout=5)
            assert web.job_store.get(job_id)['status'] == 'running'
        # The originating client is gone. No request drives completion.
        release.set()
        wait_until(lambda: web.job_store.get(job_id)['status'] == 'succeeded')
        with web.app.test_client() as reopened:
            snapshot = reopened.get('/api/tasks/' + task['id']).get_json()
            assert snapshot['completed'] == snapshot['succeeded'] == 1
            assert reopened.get('/api/queue?import_date=' + task['created_at'][:10]).get_json()[0]['status'] == 'succeeded'
    finally:
        release.set(); worker.stop()


def test_upload_retry_does_not_create_another_file_or_job(web_queue):
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 1,
        'items': [{'filename': 'r.jpg'}]}).get_json()
    job_id = task['items'][0]['id']
    for _ in range(2):
        response = client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b'image'), 'r.jpg')})
        assert response.status_code in (200, 202)
    assert len(list(web_queue.UPLOAD_DIR.iterdir())) == 1
    assert web_queue.job_store.task(task['id'])['ready'] == 1
    assert web_queue.job_store.task(task['id'])['queued'] == 0
    assert client.post('/api/analyze', data={'task_id': task['id'], 'sample': 'r.jpg'}).status_code == 409


def test_cancel_endpoint_deletes_unfinished_job_and_upload(web_queue):
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 1,
        'items': [{'filename': 'receipt.jpg'}], 'ocr_backend': 'vision'}).get_json()
    job_id = task['items'][0]['id']
    response = client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b'image'), 'receipt.jpg')})
    assert response.status_code == 202
    stored_name = web_queue.job_store.get(job_id)['stored_name']
    assert (web_queue.UPLOAD_DIR / stored_name).exists()

    response = client.post(f'/api/jobs/{job_id}/cancel')
    assert response.status_code == 200
    assert response.get_json()['deleted'] is True
    assert not (web_queue.UPLOAD_DIR / stored_name).exists()
    with pytest.raises(KeyError):
        web_queue.job_store.get(job_id)
    assert client.post(f'/api/jobs/{job_id}/cancel').status_code == 404


def test_cancel_all_endpoint_deletes_selected_unfinished_jobs(web_queue):
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 2,
        'items': [{'filename': 'one.jpg'}, {'filename': 'two.jpg'}], 'ocr_backend': 'vision'}).get_json()
    ids = [item['id'] for item in task['items']]
    response = client.post('/api/jobs/cancel', json={'ids': ids})
    assert response.status_code == 200
    assert set(response.get_json()['deleted_ids']) == set(ids)
    assert web_queue.job_store.task(task['id'])['items'] == []


def test_start_endpoint_validates_exact_selection_and_upload_does_not_wake(web_queue, monkeypatch):
    wakes = []
    monkeypatch.setattr(web_queue, '_wake_jobs', lambda: wakes.append(True))
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 3,
        'items': [{'filename': name} for name in ('a.jpg', 'b.jpg', 'c.jpg')]}).get_json()
    first, second, missing_upload = [item['id'] for item in task['items']]
    for job_id, name in [(first, 'a.jpg'), (second, 'b.jpg')]:
        response = client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b'image'), name)})
        assert response.get_json()['status'] == 'ready'
    assert wakes == []
    for bad in ({}, [], {'ids': []}, {'ids': ['']}, {'ids': [True]}, {'ids': 'all'}):
        assert client.post('/api/jobs/start', json=bad).status_code == 400
    assert client.post('/api/jobs/start', json={'ids': [first, 'unknown']}).status_code == 404
    assert client.post('/api/jobs/start', json={'ids': [first, missing_upload]}).status_code == 409
    assert wakes == []
    assert web_queue.job_store.claim() is None
    response = client.post('/api/jobs/start', json={'ids': [first]})
    assert response.status_code == 202
    payload = response.get_json()
    assert payload['started'] == 1
    assert [(item['id'], item['status']) for item in payload['items']] == [(first, 'queued')]
    assert payload['control']['ready'] == payload['control']['queued'] == 1
    assert wakes == [True]
    assert web_queue.job_store.public(web_queue.job_store.get(second))['status'] == 'ready'
    assert client.post('/api/jobs/start', json={'ids': [first]}).get_json()['started'] == 0


def test_empty_or_missing_upload_leaves_placeholder_for_reupload(web_queue):
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 1,
        'items': [{'filename': 'r.jpg'}]}).get_json()
    job_id = task['items'][0]['id']
    assert client.post(f'/api/jobs/{job_id}/upload').status_code == 400
    assert client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b''), 'r.jpg')}).status_code == 400
    assert web_queue.job_store.get(job_id)['status'] == 'awaiting_upload'
    assert list(web_queue.UPLOAD_DIR.iterdir()) == []


def test_reupload_checks_the_expected_filename(web_queue):
    client = web_queue.app.test_client()
    task = client.post('/api/tasks', json={'background': True, 'total': 1,
        'items': [{'filename': 'folder/expected.jpg'}]}).get_json()
    job_id = task['items'][0]['id']
    response = client.post(f'/api/jobs/{job_id}/upload', data={'file': (io.BytesIO(b'image'), 'wrong.jpg')})
    assert response.status_code == 400
    assert web_queue.job_store.get(job_id)['status'] == 'awaiting_upload'
    assert list(web_queue.UPLOAD_DIR.iterdir()) == []


def test_bulk_retry_endpoint_enqueues_without_running_recognition(web_queue):
    store = web_queue.job_store
    queued(store)
    first = store.claim(); store.finish(first, result())
    record_id = store.get(first['id'])['result_id']
    (web_queue.UPLOAD_DIR / 'saved.jpg').write_bytes(b'image')
    response = web_queue.app.test_client().post('/api/results/bulk-retry', json={'background': True, 'ids': [record_id]})
    assert response.status_code == 202
    assert response.get_json()['queued'] == 1
    assert store.database.get_result(record_id)['attempt'] == 1


def test_nested_folder_uploads_keep_same_basename_separate(web_queue):
    client = web_queue.app.test_client()
    names = ['回单/甲/7123.jpg', '回单/乙/九月/7123.jpg']
    task = client.post('/api/tasks', json={'background': True, 'total': 2,
        'items': [{'filename': name} for name in names]}).get_json()
    stored = []
    for index, job in enumerate(task['items']):
        response = client.post(f"/api/jobs/{job['id']}/upload", data={'file': (io.BytesIO(f'image-{index}'.encode()), '7123.jpg')})
        assert response.status_code == 202
        current = web_queue.job_store.get(job['id'])
        assert current['filename'] == names[index]
        stored.append(current['stored_name'])
    assert len(set(stored)) == 2
    assert [(web_queue.UPLOAD_DIR / name).read_bytes() for name in stored] == [b'image-0', b'image-1']


def test_pause_finishes_current_job_and_resumes_remaining_without_reprocessing(store):
    task = batch(store, total=2)
    for item in task['items']:
        store.accept_upload(item['id'], 'saved.jpg')
    store.start_jobs([item['id'] for item in task['items']])
    first = store.claim()
    control = store.set_paused(True)
    assert control['status'] == 'pausing' and control['running'] == 1 and control['queued'] == 1
    assert store.claim() is None
    assert store.finish(first, result())
    assert store.control()['status'] == 'paused'
    assert store.claim() is None
    restarted = JobStore(store.database)
    restarted.initialize()
    assert restarted.control()['paused'] is True
    assert restarted.claim() is None
    restarted.set_paused(False)
    second = restarted.claim()
    assert second['id'] != first['id']
    assert second['attempt'] == 1
    assert store.get(first['id'])['status'] == 'succeeded'


def test_paused_queue_accepts_new_uploads_and_recovery_without_starting(store):
    job = queued(store)
    store.claim()
    store.set_paused(True)
    store.recover()
    assert store.get(job['id'])['status'] == 'queued'
    assert store.claim() is None
    queued(store, task_id='new-task')
    assert store.control()['queued'] == 2
    assert store.claim() is None


def test_queue_control_endpoint_validates_boolean_and_wakes_only_on_resume(web_queue, monkeypatch):
    wakes = []
    monkeypatch.setattr(web_queue, '_wake_jobs', lambda: wakes.append(True))
    client = web_queue.app.test_client()
    assert client.get('/api/queue/control').get_json()['paused'] is False
    assert client.post('/api/queue/control', json={'paused': True}).get_json()['status'] == 'paused'
    assert wakes == []
    for bad in ({}, {'paused': 'false'}, {'paused': 1}, ['paused']):
        assert client.post('/api/queue/control', json=bad).status_code == 400
    assert client.post('/api/queue/control', json={'paused': False}).get_json()['paused'] is False
    assert wakes == [True]


def test_both_pages_can_retry_without_false_review_conflicts(store):
    pages = [dict(result(), document_type={"type": kind})
             for kind in ("receipt", "product_continuation")]
    task = store.create_batch("pages", "two pages", [{"filename": name} for name in ("r.jpg", "r_01.jpg")], OPTIONS)
    ids = []
    for item, page in zip(task["items"], pages):
        store.accept_upload(item["id"], item["filename"])
        store.start_jobs([item['id']])
        store.finish(store.claim(), deepcopy(page))
        ids.append(store.get(item["id"])["result_id"])
    store.create_retries("both", ids, {i: OPTIONS for i in ids})
    for page in pages:
        assert store.finish(store.claim(), deepcopy(page))
    assert [item["status"] for item in store.task("both")["items"]] == ["succeeded", "succeeded"]
    assert [store.database.get_result(i)["attempt"] for i in ids] == [2, 2]


def test_retry_ignores_derived_page_metadata_but_keeps_business_snapshot(store):
    record_id, job = prepare_retry(store)
    with store.database.connect() as connection:
        row = connection.execute("SELECT * FROM results WHERE id=?", (record_id,)).fetchone()
        payload = json.loads(row["result_json"])
        payload.update(page_group_id="derived:group", page_group={"page_count": 2}, page_role="cover")
        connection.execute("UPDATE results SET result_json=? WHERE id=?", (json.dumps(payload), record_id))
    assert store.finish(job, result())
    assert store.get(job["id"])["status"] == "succeeded"


@pytest.mark.parametrize("edited", [False, True])
def test_legacy_retry_revisions_upgrade_only_if_source_is_unchanged(store, edited):
    record_id, job = prepare_retry(store)
    with store.database.connect() as connection:
        row = connection.execute("SELECT * FROM results WHERE id=?", (record_id,)).fetchone()
        legacy_revision = result_revision(row, legacy=True)
        connection.execute("UPDATE recognition_jobs SET source_revision=? WHERE id=?", (legacy_revision, job["id"]))
    if edited:
        current = store.database.get_result(record_id)
        current["fields"]["客户名称"] = "人工修正客户"
        store.database.review_result(record_id, result=current, review_status="确认通过", final_result="通过",
                                     note="人工确认", action="人工复核")
    store.initialize()
    revision = store.get(job["id"])["source_revision"]
    assert revision.startswith("v2:") is (not edited)
    assert store.finish(job, result())
    assert store.get(job["id"])["status"] == ("failed" if edited else "succeeded")
    if edited:
        assert store.database.get_result(record_id)["fields"]["客户名称"] == "人工修正客户"
