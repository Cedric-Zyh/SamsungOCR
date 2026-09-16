"""Progress is readable while requests are in flight, without live services/storage."""
import json
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from receipt_ocr import danzhengtong as dzt
from receipt_ocr.database import Database
from receipt_ocr.job_store import JobStore
from receipt_ocr.job_worker import JobWorker
from receipt_ocr.recognition_progress import progress_reporting, report_dzt_progress


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / 'results.db')
    database.initialize()
    store = JobStore(database)
    store.initialize()
    options = dict(ocr_backend='vision', seal_recognition_mode='local',
                   recognition_config={'fields': ['danzhengtong']})
    task = store.create_batch('progress-test', '进度测试', [{'filename': 'sample.jpg'}], options)
    job_id = task['items'][0]['id']
    store.accept_upload(job_id, 'sample.jpg')
    store.start_jobs([job_id])
    return store


def settings():
    return dzt.Settings(app_id='test-app-id', app_key='test-app-key', app_secret='test-app-secret',
                        key_id='test-gateway-key', callback_url='https://example.com/callback')


def response(body):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def test_queue_api_reports_actual_stage_while_provider_is_still_running(store, tmp_path, monkeypatch):
    import app as web
    source = tmp_path / 'sample.jpg'
    source.write_bytes(b'image')
    waiting, release = Event(), Event()
    observed = []
    calls = []
    job_id = store.task('progress-test')['items'][0]['id']

    def request(method, url, **kwargs):
        calls.append(method)
        progress = store.public(store.get(job_id))['progress']
        observed.append(progress['stage'])
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'uploaded-file'})
        if method == 'POST':
            return response({'code': 200, 'status': True, 'data': {'reqUuid': 'accepted-request'}})
        if calls.count('GET') == 1:
            return response({'code': 200, 'data': {'commitResult': {}}})
        waiting.set()
        assert release.wait(5)
        return response({'code': 200, 'data': {'commitResult': {'客户名称': {'value': '测试客户'}}}})

    client = dzt.Client(settings(), transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt.time, 'sleep', lambda _: None)
    monkeypatch.setattr(web, 'job_store', store)

    def execute(job):
        normalized, _ = client.recognize(source)
        return {'fields': normalized['fields'], 'overall': '需人工复核', 'review_status': '待复核'}, ''

    worker = JobWorker(store, execute)
    worker.start()
    try:
        assert waiting.wait(5)
        day = store.task('progress-test')['created_at'][:10]
        payload = web.app.test_client().get(f'/api/queue?import_date={day}').get_json()[0]
        assert payload['status'] == 'running'
        assert payload['progress']['stage'] == 'waiting'
        assert payload['progress']['poll_count'] == 1
        assert payload['progress']['timeout_seconds'] == 60
        assert payload['progress']['elapsed_seconds'] >= 0
        assert store.database.list_results() == []
        assert settings().app_secret not in json.dumps(payload)
    finally:
        release.set()
        worker.stop()
    assert observed == ['uploading', 'submitting', 'waiting', 'waiting']
    finished = store.public(store.get(job_id))
    assert finished['status'] == 'succeeded'
    assert finished['progress']['stage'] == 'completed'
    assert calls == ['POST', 'POST', 'GET', 'GET']


def test_wait_elapsed_survives_polls_and_reopen_but_resets_for_recovered_attempt(store, monkeypatch):
    job = store.claim()
    event = {'provider': 'danzhengtong', 'stage': 'waiting', 'updated_at': '2026-09-11T19:00:00+08:00'}
    assert store.update_progress(job, event)
    assert store.update_progress(job, {**event, 'updated_at': '2026-09-11T19:01:00+08:00', 'poll_count': 20})
    monkeypatch.setattr('receipt_ocr.recognition_progress.now_iso', lambda: '2026-09-11T19:01:30+08:00')
    reopened = JobStore(Database(store.database.path))
    progress = reopened.public(reopened.get(job['id']))['progress']
    assert progress['elapsed_seconds'] == 90
    assert progress['poll_count'] == 20
    reopened.recover()
    assert reopened.public(reopened.get(job['id']))['progress'] == {}
    current = reopened.claim()
    assert current['attempt'] == job['attempt'] + 1
    assert not store.update_progress(job, event)
    assert reopened.public(reopened.get(job['id']))['progress'] == {}
    reopened.finish(current, error='测试结束')
    assert not reopened.update_progress(current, event)
    reopened.retry_failed(current['id'])
    assert reopened.public(reopened.get(current['id']))['progress'] == {}


def test_progress_column_migrates_existing_jobs_idempotently(store):
    job_id = store.task('progress-test')['items'][0]['id']
    with store.database.connect() as con:
        con.execute('ALTER TABLE recognition_jobs DROP COLUMN progress_json')
    store.initialize()
    store.initialize()
    assert store.public(store.get(job_id))['progress'] == {}
    assert store.get(job_id)['status'] == 'queued'


def test_provider_failure_replaces_active_progress_and_keeps_sanitized_error(tmp_path, monkeypatch):
    source = tmp_path / 'sample.jpg'
    source.write_bytes(b'image')
    cfg = settings()
    calls, events = [], []
    def request(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError(f'connection refused {cfg.app_secret}')
    client = dzt.Client(cfg, transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt, 'Client', lambda: client)
    cache = {}
    with progress_reporting(events.append):
        for stage in ('fields', 'date', 'seal'):
            with pytest.raises(RuntimeError, match='connection refused'):
                dzt.stage_result(SimpleNamespace(source=source, filename=source.name), stage, cache)
    assert [event['stage'] for event in events] == ['uploading', 'failed']
    assert 'connection refused' in events[-1]['error_message']
    assert cfg.app_secret not in events[-1]['error_message']
    assert calls == [1]
    report_dzt_progress('waiting')
    assert len(events) == 2  # Reporting context cannot leak to the next job.


def test_progress_write_failure_does_not_fail_or_resubmit_recognition(tmp_path, monkeypatch):
    source = tmp_path / 'sample.jpg'
    source.write_bytes(b'image')
    calls = []
    def request(method, url, **kwargs):
        calls.append(method)
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'file'})
        if method == 'POST':
            return response({'code': 200, 'status': True, 'data': {'reqUuid': 'request'}})
        return response({'code': 200, 'data': {'commitResult': {'客户名称': {'value': '客户'}}}})
    def fail_progress(_):
        raise RuntimeError('progress store busy')
    monkeypatch.setattr(dzt.time, 'sleep', lambda _: None)
    with progress_reporting(fail_progress):
        result, _ = dzt.Client(settings(), transport=SimpleNamespace(request=request)).recognize(source)
    assert result['fields']['客户名称'] == '客户'
    assert calls == ['POST', 'POST', 'GET']


def test_wait_timeout_switches_progress_to_the_real_error(tmp_path, monkeypatch):
    source = tmp_path / 'sample.jpg'
    source.write_bytes(b'image')
    def request(method, url, **kwargs):
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'file'})
        if method == 'POST':
            return response({'code': 200, 'status': True, 'data': {'reqUuid': 'request'}})
        return response({'code': 200, 'data': {'commitResult': None}})
    client = dzt.Client(replace(settings(), poll_timeout_seconds=3), transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt, 'Client', lambda: client)
    elapsed = [0]
    monkeypatch.setattr(dzt.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(dzt.time, 'monotonic', lambda: elapsed[0])
    events = []
    with progress_reporting(events.append), pytest.raises(RuntimeError, match='等待识别结果超时'):
        dzt.stage_result(SimpleNamespace(source=source, filename=source.name), 'fields', {})
    assert events[-2]['stage'] == 'waiting'
    assert events[-2]['poll_count'] == 1
    assert events[-1]['stage'] == 'failed'
    assert events[-1]['error_message'] == '单证通等待识别结果超时（3 秒），已停止查询；reqUuid=request'


@pytest.mark.parametrize('pause_at', ['delay', 'response'])
@pytest.mark.parametrize('resume', [True, False])
def test_pause_stops_queries_and_resume_uses_same_request_without_resetting_deadline(
        store, tmp_path, monkeypatch, pause_at, resume):
    from datetime import datetime, timedelta
    from receipt_ocr.document_context import DocumentContext
    elapsed, calls, events = [0.0], [], []
    pause_started = [False]
    source = tmp_path / 'sample.jpg'; source.write_bytes(b'image')
    job = store.claim()

    def sleep(seconds):
        elapsed[0] += seconds
        if pause_at == 'delay' and not pause_started[0]:
            pause_started[0] = True
            store.set_paused(True)
        if seconds <= .2:
            progress = store.public(store.get(job['id']))['progress']
            assert progress['stage'] == 'paused'
            assert progress['started_at'] == events[2]['updated_at']
            if resume and elapsed[0] >= 3:
                store.set_paused(False)

    def request(method, url, **kwargs):
        calls.append((method, elapsed[0], kwargs.get('params')))
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'file'})
        if method == 'POST':
            return response({'code': 200, 'status': True, 'data': {'reqUuid': 'same-request'}})
        assert not store.is_paused(), 'A paused queue must never initiate a query'
        assert elapsed[0] < 6, 'The deadline must not move when resumed'
        if pause_at == 'response' and not pause_started[0]:
            pause_started[0] = True
            store.set_paused(True)
            return response({'code': 200, 'data': {'commitResult': None}})
        return response({'code': 200, 'data': {'commitResult': {'客户名称': {'value': '客户'}}}})

    client = dzt.Client(replace(settings(), poll_timeout_seconds=6), transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt, 'Client', lambda: client)
    monkeypatch.setattr(dzt.time, 'sleep', sleep)
    monkeypatch.setattr(dzt.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr('receipt_ocr.recognition_progress.now_iso', lambda:
        (datetime.fromisoformat('2026-09-11T19:00:00+08:00') + timedelta(seconds=elapsed[0])).isoformat())
    def update(event):
        events.append(event)
        store.update_progress(job, event)
    with progress_reporting(update, paused=store.is_paused):
        if resume:
            assert dzt.stage_result(DocumentContext(source), 'fields', {})['fields']['客户名称'] == '客户'
        else:
            with pytest.raises(RuntimeError, match='超时（6 秒），已停止查询'):
                dzt.stage_result(DocumentContext(source), 'fields', {})
            assert elapsed[0] == pytest.approx(6)
    assert any(event['stage'] == 'paused' for event in events)
    assert events[-1]['stage'] == ('completed' if resume else 'failed')
    assert [call[0] for call in calls].count('POST') == 2
    assert all(call[2]['reqUuid'] == 'same-request' for call in calls if call[0] == 'GET')
    assert [call[0] for call in calls].count('GET') == int(pause_at == 'response') + int(resume)


def test_legacy_five_minute_config_cannot_extend_deadline_and_last_request_uses_remaining_time(tmp_path, monkeypatch):
    elapsed, queries = [0.0], []
    source = tmp_path / 'sample.jpg'; source.write_bytes(b'image')
    def request(method, url, **kwargs):
        if 'files' in kwargs:
            return response({'status': True, 'fileId': 'file'})
        if method == 'POST':
            return response({'code': 200, 'status': True, 'data': {'reqUuid': 'request'}})
        queries.append((elapsed[0], kwargs['timeout'].total))
        elapsed[0] += 55 if len(queries) == 1 else 1
        return response({'code': 200, 'data': {'commitResult': None}})
    client = dzt.Client(replace(settings(), poll_timeout_seconds=300), transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    monkeypatch.setattr(dzt.time, 'monotonic', lambda: elapsed[0])
    with pytest.raises(RuntimeError, match='超时（60 秒），已停止查询'):
        client.recognize(source)
    assert client.poll_timeout == 60
    assert queries == [(2, 58), (59, 1)]
    assert elapsed[0] == 60
