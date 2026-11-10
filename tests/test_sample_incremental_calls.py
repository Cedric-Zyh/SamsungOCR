"""Supplementary remote recognition keeps the measured calls of retained methods."""
from copy import deepcopy
import json
import sqlite3

from sample_support import SampleCenter
from receipt_ocr.samples.execution import sample_execution_options
from receipt_ocr.persistence.async_recognition_calls import merge_remote_snapshot
from test_sample_center import samples
from test_sample_provider_preservation import seed_legacy
from test_danzhengtong_jobs import FakeClient
from test_danzhengtong_jobs import runtime, start, finish_regular
from test_receipt_runs import trace


def recorded_methods():
    snapshot = {**trace(), 'model_initialization_seconds': .1,
                'image_processing': {'seconds': .2, 'steps': []}}
    snapshot['calls'].append({**snapshot['calls'][0], 'id': 2, 'method': 'qingtong',
                              'method_label': '清瞳', 'input_name': 'saved-seal.png'})
    snapshot['summary'] = {'total': 2, 'succeeded': 2, 'failed': 0, 'cache_hits': 2}
    return snapshot


def current_job(service, filename='one.png'):
    with service.database.connect() as con:
        return dict(con.execute('SELECT * FROM recognition_jobs WHERE filename=? ORDER BY rowid DESC LIMIT 1',
                                (filename,)).fetchone())


def test_supplement_retains_other_methods_and_updates_one_remote_call_across_queries(samples):
    ctx, client = samples
    service, original_job, _ = seed_legacy(ctx)
    original_trace = recorded_methods()
    assert service.database.save_recognition_calls('one.png', original_trace, result_id=original_job['result_id'],
                                                  run_key=f"{original_job['id']}:{original_job['attempt']}")
    assert client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'}).status_code == 202
    service = SampleCenter(ctx)
    job = current_job(service)
    assert service.store.claim() is None  # No local recognition is rerun.
    provider = FakeClient()
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()
    waiting = service.database.latest_recognition_calls(job['result_id'])
    assert waiting['status'] == 'running' and waiting['incremental']
    assert [row['method'] for row in waiting['calls']] == ['paddle_v6', 'qingtong', 'danzhengtong']
    assert [{key: value for key, value in row.items() if key != '_run'}
            for row in waiting['calls'][:2]] == original_trace['calls']
    assert all(row['_run']['retained'] for row in waiting['calls'][:2])
    assert waiting['calls'][0]['_run']['finished_at'] == original_trace['finished_at']
    assert waiting['image_processing'] == original_trace['image_processing']
    assert waiting['summary'] == {'total': 3, 'succeeded': 2, 'failed': 0, 'cache_hits': 2}
    provider.query_error = TimeoutError('temporary')
    service.store.remote_action([job['id']], 'query')
    assert service.worker.remote.step()
    assert service.database.latest_recognition_calls(job['result_id'])['summary']['total'] == 3
    provider.query_error, provider.ready = None, True
    service.store.remote_action([job['id']], 'query')
    assert service.worker.remote.step()
    assert service.worker.remote.step()
    completed = client.get(f"/api/samples/results/{job['result_id']}/recognition-calls").json
    assert completed['summary'] == {'total': 3, 'succeeded': 3, 'failed': 0, 'cache_hits': 2}
    assert completed['calls'][:2] == waiting['calls'][:2]
    assert completed['result_id'] == job['result_id']
    assert completed['model_initialization_seconds'] == 0  # Old initialization belongs to the retained run.
    remote_timing = completed['calls'][-1]['transport']
    from datetime import datetime
    assert completed['processing_seconds'] == round((datetime.fromisoformat(remote_timing['applied_at'])
        - datetime.fromisoformat(remote_timing['started_at'])).total_seconds(), 4)
    assert completed == client.get(f"/api/samples/results/{job['result_id']}/recognition-calls").json
    assert provider.submits == provider.uploads == 1 and provider.queries == 2
    assert service.database.get_result(original_job['result_id'])['fields']['客户名称'] == '原客户'
    # A later full local test follows the existing latest-run replacement rule.
    settings = ctx.database.get_json_setting('recognition_defaults')
    options = sample_execution_options(settings, 'local', api_enabled=False)
    service.store.enqueue(['one.png'], options, settings)
    latest = service.store.claim()
    service.store.finish(latest, {'fields': {}, '_recognition_calls': trace(name='new-local.png')})
    fresh = service.database.latest_recognition_calls(service.store.get(latest['id'])['result_id'])
    assert fresh['summary']['total'] == 1 and fresh['calls'][0]['input_name'] == 'new-local.png'
    assert not fresh.get('incremental')


def test_legacy_accepted_request_is_visible_without_inventing_lost_local_calls_or_timing(samples):
    ctx, client = samples
    service, _, _ = seed_legacy(ctx)
    client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    service = SampleCenter(ctx)
    job = current_job(service)
    with service.database.connect() as con:
        con.execute("UPDATE danzhengtong_jobs SET status='applied',req_uuid='already-accepted',submitted_at=?,last_query_at=? WHERE job_id=?",
                    (job['created_at'], job['created_at'], job['id']))
    for _ in range(2):
        data = service.database.latest_recognition_calls(job['result_id'])
        assert data['available'] and data['summary']['total'] == data['summary']['succeeded'] == 1
        assert data['calls'][0]['method'] == 'danzhengtong' and data['calls'][0]['seconds'] is None
        assert data['processing_seconds'] is None and data['model_initialization_seconds'] is None
        assert '无法恢复' in data['note'] and '未采集逐次耗时' in data['note']
        with service.database.connect() as con:
            service.database.sync_remote_recognition_calls(con, job['id'])
    with service.database.connect() as con:
        assert con.execute('SELECT recognition_call_json FROM danzhengtong_jobs WHERE job_id=?',
                           (job['id'],)).fetchone()[0] == '{}'


def test_replacing_an_old_remote_call_does_not_guess_its_share_of_wall_time():
    previous = recorded_methods()
    previous['calls'].append({**previous['calls'][0], 'method': 'danzhengtong', 'seconds': .5})
    previous['processing_seconds'] = 3
    remote = {'method': 'danzhengtong', 'status': 'succeeded', 'seconds': .7}
    merged = merge_remote_snapshot(previous, remote, incremental=True)
    assert len(merged['calls']) == 3
    assert merged['processing_seconds'] is None
    assert merge_remote_snapshot(merged, remote, incremental=True)['processing_seconds'] is None


def test_older_remote_completion_cannot_replace_a_newer_sample_tests_details(samples):
    ctx, client = samples
    service, _, _ = seed_legacy(ctx)
    client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    service = SampleCenter(ctx)
    previous = current_job(service)
    provider = FakeClient()
    service.worker.remote.client_factory = lambda task: provider
    service.worker.remote.step()
    with service.database.connect() as con:
        # A later test supersedes the old accepted remote lane.
        con.execute("UPDATE danzhengtong_jobs SET status='applied' WHERE job_id=?", (previous['id'],))
    settings = ctx.database.get_json_setting('recognition_defaults')
    service.store.enqueue(['one.png'], sample_execution_options(settings, 'local', api_enabled=False), settings)
    latest = service.store.claim()
    service.store.finish(latest, {'fields': {}, '_recognition_calls': trace(name='newer.png')})
    before = service.database.latest_recognition_calls(service.store.get(latest['id'])['result_id'])
    with service.database.connect() as con:
        service.database.sync_remote_recognition_calls(con, previous['id'])
    assert service.database.latest_recognition_calls(service.store.get(latest['id'])['result_id']) == before


def test_remote_submission_failure_is_one_failed_call_and_preserves_local_details(samples):
    ctx, client = samples
    service, original, _ = seed_legacy(ctx)
    assert service.database.save_recognition_calls('one.png', recorded_methods(), result_id=original['result_id'],
                                                  run_key=f"{original['id']}:{original['attempt']}")
    client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    service = SampleCenter(ctx)
    job = current_job(service)
    provider = FakeClient()
    provider.submit_error = TimeoutError('secret provider diagnostic')
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()
    data = service.database.latest_recognition_calls(job['result_id'])
    assert data['summary'] == {'total': 3, 'succeeded': 2, 'failed': 1, 'cache_hits': 2}
    assert data['calls'][-1]['status'] == 'failed'
    assert 'secret' not in json.dumps(data)
    assert provider.uploads == provider.submits == 1


def test_remote_submitted_before_regular_finish_is_merged_with_that_runs_calls(runtime):
    job_id = start(runtime, mixed=True)
    assert runtime.worker.step()
    claimed = runtime.store.claim()
    payload, preview = runtime.service(claimed)
    payload['_recognition_calls'] = recorded_methods()
    runtime.store.finish(claimed, payload, preview)
    job = runtime.store.get(job_id)
    detail = runtime.store.database.latest_recognition_calls(job['result_id'])
    assert [call['method'] for call in detail['calls']] == ['paddle_v6', 'qingtong', 'danzhengtong']
    assert detail['status'] == 'running' and detail['summary']['total'] == 3
    runtime.client.ready = True
    runtime.store.remote_action([job_id], 'query')
    assert runtime.worker.step()
    assert runtime.worker.step()
    detail = runtime.store.database.latest_recognition_calls(job['result_id'])
    assert detail['summary']['total'] == detail['summary']['succeeded'] == 3
    assert runtime.client.uploads == runtime.client.submits == 1


def test_failed_query_persistence_does_not_add_the_same_request_duration_twice(runtime, monkeypatch):
    from receipt_ocr.jobs import danzhengtong_worker
    job_id = start(runtime)
    runtime.worker.step()
    finish_regular(runtime)
    runtime.client.ready = True
    with runtime.store.database.connect() as con:
        con.execute('UPDATE danzhengtong_jobs SET recognition_call_json=? WHERE job_id=?',
                    (json.dumps({'method': 'danzhengtong', 'seconds': 3, 'timing_recorded': True}), job_id))
    original = runtime.store.update_remote
    writes = []
    clock = iter([0, 2, 4, 6])
    monkeypatch.setattr(danzhengtong_worker, 'perf_counter', lambda: next(clock))

    def fail_received_once(task, **changes):
        if changes.get('status') == 'received':
            writes.append(changes['recognition_call_json'])
            raise sqlite3.OperationalError('busy')
        return original(task, **changes)

    monkeypatch.setattr(runtime.store, 'update_remote', fail_received_once)
    runtime.store.remote_action([job_id], 'query')
    assert runtime.worker.step()
    with runtime.store.database.connect() as con:
        measured = json.loads(con.execute('SELECT recognition_call_json FROM danzhengtong_jobs WHERE job_id=?',
                                          (job_id,)).fetchone()[0])
    assert json.loads(writes[0])['seconds'] == measured['seconds'] == 5
    assert runtime.client.queries == 1
