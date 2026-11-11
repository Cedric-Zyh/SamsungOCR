"""Async HTTP phases are measured once and survive waits, restarts and failures."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sqlite3

import pytest

from receipt_ocr.jobs.danzhengtong_worker import DanzhengtongWorker
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from receipt_ocr.persistence.async_recognition_calls import remote_call, merge_remote_snapshot
from test_danzhengtong_jobs import FakeClient, runtime, start, finish_regular


@pytest.fixture
def timed(runtime, monkeypatch):
    from receipt_ocr.jobs import danzhengtong_worker
    clock = SimpleNamespace(seconds=0.0)
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Client(FakeClient):
        upload_error = False

        def upload_file(self, *args, **kwargs):
            clock.seconds += 3
            result = super().upload_file(*args, **kwargs)
            if self.upload_error:
                raise TimeoutError('secret uploaded file URL')
            return result

        def submit(self, *args, **kwargs):
            clock.seconds += 2
            return super().submit(*args, **kwargs)

        def get_result(self, *args, **kwargs):
            clock.seconds += 4
            return super().get_result(*args, **kwargs)

    provider = Client()
    runtime.worker.client_factory = lambda task: provider
    runtime.client = provider
    monkeypatch.setattr(danzhengtong_worker, 'perf_counter', lambda: clock.seconds)
    monkeypatch.setattr(danzhengtong_worker, 'trace_timestamp',
                        lambda: (epoch + timedelta(seconds=clock.seconds)).isoformat(timespec='microseconds'))
    monkeypatch.setattr('receipt_ocr.persistence.recognition_calls.trace_timestamp',
                        lambda: (epoch + timedelta(seconds=clock.seconds)).isoformat(timespec='microseconds'))
    return runtime, provider, clock


def saved_call(service, job_id):
    with service.store.database.connect() as con:
        row = con.execute('SELECT recognition_call_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                          (job_id,)).fetchone()
    return json.loads(row[0])


def query(service, job_id):
    service.store.remote_action([job_id], 'query')
    assert service.worker.step()


def test_upload_submit_query_and_whole_lifecycle_are_distinct_and_survive_restart(timed):
    service, provider, clock = timed
    job_id = start(service)
    assert service.worker.step()
    accepted = saved_call(service, job_id)
    assert accepted['seconds'] == 2
    assert {key: accepted['transport'][key] for key in
            ('upload_seconds', 'submit_seconds', 'query_seconds', 'query_count', 'request_seconds')} == {
                'upload_seconds': 3, 'submit_seconds': 2, 'query_seconds': 0, 'query_count': 0, 'request_seconds': 5}
    assert accepted['transport']['elapsed_seconds'] is None
    assert [(span['id'], span['seconds']) for span in accepted['transport']['spans']] == [
        ('upload:1', 3), ('submit:1', 2)]
    job = finish_regular(service)
    reopened = JobStore(Database(service.store.database.path))
    reopened.database.initialize()
    reopened.initialize()
    reopened.recover_remote()
    service.store = reopened
    service.worker = DanzhengtongWorker(reopened, service.service, client_factory=lambda task: provider)
    clock.seconds += 100  # Includes time spent waiting or with the process closed.
    provider.ready = True
    query(service, job_id)
    completed = saved_call(service, job_id)
    timing = completed['transport']
    assert timing['upload_seconds'] == 3 and timing['submit_seconds'] == 2
    assert timing['query_seconds'] == 4 and timing['query_count'] == 1
    assert timing['request_seconds'] == timing['measured_request_seconds'] == 9
    assert timing['elapsed_seconds'] == 109 and timing['elapsed_scope'] == 'upload_to_result'
    assert timing['complete'] is True
    assert [(span['id'], span['started_at'], span['finished_at'], span['seconds'])
            for span in timing['spans']] == [
        ('upload:1', '2026-01-01T00:00:00.000000+00:00', '2026-01-01T00:00:03.000000+00:00', 3),
        ('submit:1', '2026-01-01T00:00:03.000000+00:00', '2026-01-01T00:00:05.000000+00:00', 2),
        ('query:1', '2026-01-01T00:01:45.000000+00:00', '2026-01-01T00:01:49.000000+00:00', 4)]
    assert timing['result_received_at'] == timing['spans'][-1]['finished_at']
    assert completed['seconds'] == 6  # Existing recognition-call accounting omits upload.
    detail = reopened.database.latest_recognition_calls(job['result_id'])
    assert detail['summary']['total'] == 1 and detail['calls'][0]['transport'] == timing
    assert detail['finished_at'] == ''  # Receiving a response has not saved the combined result yet.
    clock.seconds += 7
    assert service.worker.step()
    applied = saved_call(service, job_id)['transport']
    assert applied['applied_at'] == '2026-01-01T00:01:56.000000+00:00'
    assert applied['result_received_at'] == timing['result_received_at']
    final = reopened.database.latest_recognition_calls(job['result_id'])
    assert final['processing_seconds'] == 116  # Includes waiting and saving, not the request sum.
    assert provider.uploads == provider.submits == provider.queries == 1


def test_failed_query_time_accumulates_but_waiting_intervals_do_not_add_http_time(timed):
    service, provider, clock = timed
    job_id = start(service)
    service.worker.step()
    finish_regular(service)
    clock.seconds += 20
    provider.query_error = TimeoutError('secret')
    query(service, job_id)
    waiting = saved_call(service, job_id)
    assert waiting['transport']['query_seconds'] == 4 and waiting['transport']['query_count'] == 1
    assert waiting['transport']['request_seconds'] == 9 and waiting['transport']['elapsed_seconds'] is None
    clock.seconds += 30
    provider.query_error, provider.ready = None, True
    query(service, job_id)
    completed = saved_call(service, job_id)
    assert completed['transport']['query_seconds'] == 8 and completed['transport']['query_count'] == 2
    assert completed['transport']['request_seconds'] == 13
    assert completed['transport']['elapsed_seconds'] == 63
    assert completed['seconds'] == 10
    assert [(span['id'], span['status']) for span in completed['transport']['spans']] == [
        ('upload:1', 'succeeded'), ('submit:1', 'succeeded'), ('query:1', 'failed'), ('query:2', 'succeeded')]
    assert provider.uploads == provider.submits == 1 and provider.queries == 2


def test_failed_upload_records_interface_time_without_inventing_an_ocr_call(timed):
    service, provider, _ = timed
    job_id = start(service)
    provider.upload_error = True
    service.worker.step()
    call = saved_call(service, job_id)
    assert call['recognition_invoked'] is False and call['seconds'] is None
    assert call['transport']['upload_seconds'] == call['transport']['request_seconds'] == 3
    assert call['transport']['submit_seconds'] is None and call['transport']['query_count'] == 0
    assert call['transport']['elapsed_seconds'] == 3
    assert call['transport']['spans'][0]['status'] == 'failed'
    job = finish_regular(service)
    with service.store.database.connect() as con:
        snapshot = json.loads(con.execute('SELECT snapshot_json FROM latest_recognition_calls WHERE filename=?',
                                          (job['filename'],)).fetchone()[0])
    assert snapshot['calls'] == [] and snapshot['summary']['total'] == 0
    assert len(snapshot['transport_calls']) == 1 and snapshot['transport_calls'][0]['status'] == 'failed'
    assert provider.uploads == 1 and provider.submits == provider.queries == 0
    assert 'secret' not in json.dumps(snapshot)


def test_failed_submission_retains_both_measured_phases_and_counts_one_attempt(timed):
    service, provider, _ = timed
    job_id = start(service)
    provider.submit_error = TimeoutError('secret key')
    service.worker.step()
    call = saved_call(service, job_id)
    assert call['recognition_invoked'] is True and call['seconds'] == 2
    assert call['transport']['upload_seconds'] == 3 and call['transport']['submit_seconds'] == 2
    assert call['transport']['request_seconds'] == call['transport']['elapsed_seconds'] == 5
    job = finish_regular(service)
    detail = service.store.database.latest_recognition_calls(job['result_id'])
    assert detail['summary']['total'] == detail['summary']['failed'] == 1
    assert not service.worker.step()  # An uncertain POST is not submitted again.
    assert provider.uploads == provider.submits == 1 and provider.queries == 0


@pytest.mark.parametrize('previous_seconds', [None, 3])
def test_legacy_phase_splits_remain_unknown_after_a_new_measured_query(timed, previous_seconds):
    service, provider, clock = timed
    job_id = start(service)
    service.worker.step()
    finish_regular(service)
    with service.store.database.connect() as con:
        call = {} if previous_seconds is None else {'method': 'danzhengtong', 'seconds': previous_seconds,
                                                    'timing_recorded': True}
        con.execute("UPDATE danzhengtong_jobs SET recognition_call_json=?,query_count=3,submitted_at=? WHERE job_id=?",
                    (json.dumps(call), '2026-01-01T00:00:00+00:00', job_id))
    clock.seconds += 100
    provider.ready = True
    query(service, job_id)
    call = saved_call(service, job_id)
    timing = call['transport']
    assert timing['upload_seconds'] is None and timing['submit_seconds'] is None
    assert timing['query_seconds'] is None and timing['query_count'] == 4
    assert timing['request_seconds'] is None and timing['complete'] is False
    assert timing['measured_request_seconds'] == (previous_seconds or 0) + 4
    assert timing['elapsed_seconds'] == 109 and timing['elapsed_scope'] == 'submission_to_result'
    assert len(timing['spans']) == 1 and timing['spans'][0]['id'] == 'query:4'
    assert timing['spans'][0]['seconds'] == 4  # Three old query positions remain unknown.
    assert call['seconds'] == (previous_seconds + 4 if previous_seconds is not None else None)
    assert provider.uploads == provider.submits == provider.queries == 1


def test_query_persistence_retry_cannot_double_add_phase_seconds_or_query_count(timed, monkeypatch):
    service, provider, _ = timed
    job_id = start(service)
    service.worker.step()
    finish_regular(service)
    provider.ready = True
    original = service.store.update_remote
    failed = []

    def write(task, **changes):
        if changes.get('status') == 'received' and not failed:
            failed.append(changes['recognition_call_json'])
            raise sqlite3.OperationalError('busy')
        return original(task, **changes)

    monkeypatch.setattr(service.store, 'update_remote', write)
    query(service, job_id)
    call = saved_call(service, job_id)
    assert call['transport'] == json.loads(failed[0])['transport']
    assert call['transport']['query_seconds'] == 4 and call['transport']['query_count'] == 1
    assert call['transport']['request_seconds'] == 9 and call['seconds'] == 6
    assert len([span for span in call['transport']['spans'] if span['phase'] == 'query']) == 1
    assert provider.queries == 1


def test_recorded_old_lifecycle_times_can_be_projected_without_any_network_request():
    remote = {'id': 'accepted', 'req_uuid': 'already-accepted', 'recognition_call_json': '{}',
              'status': 'applied', 'submitted_at': '2026-01-01T00:00:00+08:00',
              'last_query_at': '2026-01-01T00:00:23+08:00', 'query_count': 4}
    call = remote_call(remote, 'receipt.jpg')
    assert call['recognition_invoked'] is True and call['seconds'] is None
    assert call['transport']['elapsed_seconds'] == 23
    assert call['transport']['elapsed_scope'] == 'submission_to_result'
    assert call['transport']['upload_seconds'] is call['transport']['submit_seconds'] is None
    assert call['transport']['request_seconds'] is None


def test_configuration_failure_before_a_query_does_not_measure_or_count_an_http_request(timed):
    service, provider, clock = timed
    job_id = start(service)
    service.worker.step()
    finish_regular(service)
    before = saved_call(service, job_id)['transport']

    def invalid():
        raise ValueError('missing credentials')

    provider._validate_credentials = invalid
    clock.seconds += 20
    query(service, job_id)
    after = saved_call(service, job_id)['transport']
    assert after == before and provider.queries == 0
    assert after['query_count'] == 0 and after['request_seconds'] == 5
    assert service.store.remote_detail(job_id)['query_count'] == 0


def test_an_interrupted_unmeasured_upload_does_not_hide_new_measured_submit_and_query_times(timed):
    service, provider, clock = timed
    job_id = start(service)
    unfinished = {'method': 'danzhengtong', 'recognition_invoked': False, 'seconds': None,
                  'transport': {'started_at': '2026-01-01T00:00:00+00:00', 'finished_at': '',
                      'elapsed_scope': 'upload_to_result', 'elapsed_seconds': None, 'complete': True,
                      'upload_seconds': None, 'submit_seconds': None, 'query_seconds': 0, 'query_count': 0,
                      'request_seconds': 0, 'measured_request_seconds': 0, 'phase': 'uploading'}}
    with service.store.database.connect() as con:
        con.execute('UPDATE danzhengtong_jobs SET recognition_call_json=? WHERE job_id=?',
                    (json.dumps(unfinished), job_id))
    clock.seconds += 50
    service.worker.step()
    accepted = saved_call(service, job_id)
    assert accepted['transport']['upload_seconds'] is None
    assert accepted['transport']['submit_seconds'] == accepted['seconds'] == 2
    assert accepted['transport']['request_seconds'] is None
    assert accepted['transport']['measured_request_seconds'] == 5
    finish_regular(service)
    clock.seconds += 20
    provider.ready = True
    query(service, job_id)
    completed = saved_call(service, job_id)
    assert completed['transport']['upload_seconds'] is None
    assert completed['transport']['query_seconds'] == 4 and completed['transport']['query_count'] == 1
    assert completed['transport']['request_seconds'] is None
    assert completed['transport']['measured_request_seconds'] == 9
    assert completed['transport']['elapsed_seconds'] == 79


def test_an_interrupted_submission_is_not_described_as_definitely_unsubmitted():
    recorded = {'method': 'danzhengtong', 'recognition_invoked': False, 'seconds': None,
                'transport': {'phase': 'submitting', 'upload_seconds': 3, 'submit_seconds': None}}
    remote = {'id': 'unknown', 'req_uuid': '', 'recognition_call_json': json.dumps(recorded),
              'status': 'unknown', 'submitted_at': '', 'last_query_at': '', 'query_count': 0}
    call = remote_call(remote, 'receipt.jpg')
    snapshot = merge_remote_snapshot({}, call)
    assert snapshot['summary']['total'] == 0 and snapshot['transport_calls'][0]['submission_unknown']
    assert '提交状态待确认' in snapshot['note'] and '尚未提交识别' not in snapshot['note']


def timeline_local():
    return {'run_id': 'local-run', 'started_at': '2026-01-01T00:00:00+00:00',
            'finished_at': '2026-01-01T00:00:06+00:00', 'status': 'completed',
            'processing_seconds': 6, 'model_initialization_seconds': 1,
            'calls': [{'id': 1, 'method': 'paddle_v6', 'status': 'succeeded', 'seconds': 5}]}


def timeline_remote(*, status='applied'):
    return {'method': 'danzhengtong', 'status': 'succeeded', '_remote_status': status,
            '_remote_job_id': 'current-run', '_regular_required': True, 'seconds': 7,
            'transport': {'started_at': '2026-01-01T00:00:01+00:00',
                'finished_at': '2026-01-01T00:00:13+00:00',
                'result_received_at': '2026-01-01T00:00:13+00:00',
                'applied_at': '2026-01-01T00:00:14+00:00', 'elapsed_scope': 'upload_to_result'}}


def test_parallel_processing_uses_shared_wall_range_and_keeps_original_regular_end():
    merged = merge_remote_snapshot(timeline_local(), timeline_remote())
    assert merged['processing_seconds'] == 14  # Neither 6+7 nor 12 seconds of remote lifecycle.
    assert merged['recognition_seconds'] == 13
    assert merged['started_at'] == '2026-01-01T00:00:00+00:00'
    assert merged['finished_at'] == '2026-01-01T00:00:14+00:00'
    assert merged['_regular_timing']['finished_at'] == '2026-01-01T00:00:06+00:00'
    assert merged['_regular_timing']['run_id'] == 'local-run'
    assert merged['processing_finished_scope'] == 'result_applied'
    assert merged['processing_seconds_approximate'] is False
    assert merge_remote_snapshot(merged, timeline_remote()) == merged


def test_incremental_wall_range_excludes_retained_run_and_preserves_its_provenance():
    original = timeline_local()
    original['calls'][0]['_run'] = {'id': 'older-run', 'started_at': '2025-12-01T00:00:00+00:00',
                                    'finished_at': '2025-12-01T00:00:02+00:00'}
    merged = merge_remote_snapshot(original, timeline_remote(), incremental=True)
    assert merged['processing_seconds'] == 13 and merged['recognition_seconds'] == 13
    assert merged['model_initialization_seconds'] == 0
    assert merged['run_id'] == 'current-run'
    retained = merged['calls'][0]['_run']
    assert retained['id'] == 'older-run' and retained['retained']
    assert retained['finished_at'] == '2025-12-01T00:00:02+00:00'
    assert merge_remote_snapshot(merged, timeline_remote(), incremental=True) == merged
    assert '_run' not in original['calls'][0] or 'retained' not in original['calls'][0]['_run']


def _save_remote_timeline(runtime, job_id, *, offset=86400):
    call = timeline_remote()
    for key in ('started_at', 'finished_at', 'result_received_at', 'applied_at'):
        call['transport'][key] = (datetime.fromisoformat(call['transport'][key])
                                  + timedelta(seconds=offset)).isoformat()
    with runtime.store.database.connect() as con:
        row = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job_id,)).fetchone()
        state = json.loads(row['seal_requests_json'])
        state.update(prepared=True, page_required=True, seal_required=False, requests=[],
                     page={'status': 'completed'})
        con.execute('''UPDATE danzhengtong_jobs SET status='applied',req_uuid='saved-request',
            recognition_call_json=?,seal_requests_json=? WHERE id=?''',
            (json.dumps(call), json.dumps(state), row['id']))


def _save_local_timeline(runtime, job, *, offset=0):
    snapshot = timeline_local()
    for key in ('started_at', 'finished_at'):
        snapshot[key] = (datetime.fromisoformat(snapshot[key]) + timedelta(seconds=offset)).isoformat()
    snapshot['calls'][0].update(run_id='local-run', started_at=snapshot['started_at'],
                                finished_at=snapshot['finished_at'])
    with runtime.store.database.connect() as con:
        con.execute('UPDATE latest_recognition_calls SET snapshot_json=?,started_at=? WHERE filename=?',
                    (json.dumps(snapshot), snapshot['started_at'], job['filename']))
        con.execute('UPDATE recognition_jobs SET started_at=?,finished_at=? WHERE id=?',
                    (snapshot['started_at'], snapshot['finished_at'], job['id']))
    return snapshot


def test_remote_resubmit_separates_saved_local_run_and_repairs_legacy_combined_trace(runtime):
    job_id = start(runtime, mixed=True)
    job = finish_regular(runtime)
    local = _save_local_timeline(runtime, job)
    runtime.store.remote_action([job_id], 'resubmit')
    with runtime.store.database.connect() as con:
        state = json.loads(con.execute('SELECT seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                                       (job_id,)).fetchone()[0])
    assert state['retained_regular_job_id'] == job_id
    assert state['retained_regular_attempt'] == job['attempt']
    _save_remote_timeline(runtime, job_id)
    # Reproduce the pre-fix saved state: old local calls and tomorrow's remote
    # attempt were already persisted as one non-incremental 24-hour run.
    with runtime.store.database.connect() as con:
        remote = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job_id,)).fetchone()
        state = json.loads(remote['seal_requests_json'])
        state.pop('retained_regular_job_id')
        state.pop('retained_regular_attempt')
        state.pop('run_link', None)  # Recreate a save from before explicit run ownership.
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                    (json.dumps(state), remote['id']))
        polluted = merge_remote_snapshot(local, remote_call(dict(remote), job['filename']))
        assert polluted['processing_seconds'] == 86414
        saved = json.dumps(polluted)
        con.execute('UPDATE latest_recognition_calls SET snapshot_json=? WHERE filename=?', (saved, job['filename']))
    detail = runtime.store.database.latest_recognition_calls(job['result_id'])
    assert detail['incremental'] is True
    assert detail['processing_seconds'] == detail['timeline']['elapsed_seconds'] == 13
    active, retained = detail['timeline']['runs']
    assert active['kind'] == 'current' and active['started_at'] == '2026-01-02T00:00:01.000000+00:00'
    assert retained['kind'] == 'retained' and retained['elapsed_seconds'] == 6
    assert detail['calls'][0]['_run']['retained'] is True
    assert detail['summary']['total'] == 2
    with runtime.store.database.connect() as con:
        assert con.execute('SELECT snapshot_json FROM latest_recognition_calls WHERE filename=?',
                           (job['filename'],)).fetchone()[0] == saved


def test_first_remote_attempt_delayed_by_queue_remains_one_shared_run(runtime):
    job_id = start(runtime, mixed=True)
    job = finish_regular(runtime)
    _save_local_timeline(runtime, job)
    with runtime.store.database.connect() as con:
        con.execute("UPDATE danzhengtong_jobs SET created_at='2026-01-01T00:00:00+00:00' WHERE job_id=?", (job_id,))
    # Clicking submit on an already queued attempt must not turn its waiting
    # time into a new run; ensure_remote returns the original remote row.
    runtime.store.remote_action([job_id], 'submit')
    _save_remote_timeline(runtime, job_id)
    detail = runtime.store.database.latest_recognition_calls(job['result_id'])
    assert detail['incremental'] is False
    assert detail['timeline']['elapsed_seconds'] == 86414
    assert len(detail['timeline']['runs']) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_first_independent_remote_submission_after_completion_retains_local_run(runtime, legacy):
    job_id = start(runtime, mixed=True, mode='local')
    job = finish_regular(runtime)
    _save_local_timeline(runtime, job)
    runtime.store.remote_action([job_id], 'submit')
    _save_remote_timeline(runtime, job_id)
    if legacy:
        with runtime.store.database.connect() as con:
            row = con.execute('SELECT id,seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                              (job_id,)).fetchone()
            state = json.loads(row['seal_requests_json'])
            state.pop('retained_regular_job_id')
            state.pop('retained_regular_attempt')
            state.pop('run_link', None)
            con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=?,created_at=? WHERE id=?',
                        (json.dumps(state), '2026-01-02T00:00:00+00:00', row['id']))
    detail = runtime.store.database.latest_recognition_calls(job['result_id'])
    assert detail['incremental'] is True
    assert detail['timeline']['elapsed_seconds'] == 13
    assert [run['kind'] for run in detail['timeline']['runs']] == ['current', 'retained']


@pytest.mark.parametrize('scope', ['page', 'seal'])
def test_remote_attempt_transferred_to_new_local_job_does_not_retain_that_new_local_run(runtime, scope):
    job_id = start(runtime, mixed=True)
    first = finish_regular(runtime)
    _save_local_timeline(runtime, first)
    runtime.store.remote_action([job_id], 'resubmit')
    _save_remote_timeline(runtime, job_id)
    with runtime.store.database.connect() as con:
        con.execute("UPDATE danzhengtong_jobs SET created_at='2026-01-02T00:00:00+00:00' WHERE job_id=? AND active=1",
                    (job_id,))
        if scope == 'seal':
            row = con.execute('SELECT id,recognition_call_json,seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                              (job_id,)).fetchone()
            state = json.loads(row['seal_requests_json'])
            state.update(page_required=False, seal_required=True, page={'status': 'skipped'},
                         requests=[{'index': 0, 'status': 'completed', 'req_uuid': 'saved-seal',
                                    'call': json.loads(row['recognition_call_json'])}])
            con.execute("UPDATE danzhengtong_jobs SET recognition_call_json='{}',req_uuid='',seal_requests_json=? WHERE id=?",
                        (json.dumps(state), row['id']))
    options = json.loads(runtime.store.get(job_id)['payload_json'])
    task = runtime.store.create_retries('retry-local', [first['result_id']], {first['result_id']: options})
    next_job = finish_regular(runtime)
    assert next_job['id'] == task['items'][0]['id']
    _save_local_timeline(runtime, next_job, offset=172800)
    detail = runtime.store.database.latest_recognition_calls(next_job['result_id'])
    assert detail['incremental'] is False
    assert not detail['calls'][0].get('_run', {}).get('retained')
    assert all(run['kind'] == 'current' for run in detail['timeline']['runs'])
    assert detail['timeline']['elapsed_seconds'] == 6
    # Reapplying the retained result updates its save timestamp. It must not
    # reattach yesterday's upload interval to the new local run afterward.
    with runtime.store.database.connect() as con:
        row = con.execute('SELECT id,recognition_call_json,seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                          (next_job['id'],)).fetchone()
        state = json.loads(row['seal_requests_json'])
        assert state['retained_remote_result'] == {'job_id': next_job['id'], 'finished_at': '2026-01-02T00:00:14+00:00'}
        call = json.loads(row['recognition_call_json']) if scope == 'page' else state['requests'][0]['call']
        call['transport']['applied_at'] = '2026-01-03T00:00:10+00:00'
        con.execute("UPDATE danzhengtong_jobs SET status='applied',recognition_call_json=?,seal_requests_json=? WHERE id=?",
                    (json.dumps(call) if scope == 'page' else '{}', json.dumps(state), row['id']))
    applied = runtime.store.database.latest_recognition_calls(next_job['result_id'])
    assert applied['timeline']['elapsed_seconds'] == 6
    assert len(applied['calls']) == 1


@pytest.mark.parametrize('legacy', [False, True])
def test_new_local_attempt_on_same_job_does_not_inherit_remote_resubmit_provenance(runtime, legacy):
    job_id = start(runtime, mixed=True)
    first = finish_regular(runtime)
    _save_local_timeline(runtime, first)
    runtime.store.remote_action([job_id], 'resubmit')
    _save_remote_timeline(runtime, job_id)
    with runtime.store.database.connect() as con:
        row = con.execute('SELECT id,seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                          (job_id,)).fetchone()
        state = json.loads(row['seal_requests_json'])
        if legacy:
            state.pop('retained_regular_job_id')
            state.pop('retained_regular_attempt')
            state.pop('run_link', None)
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=?,created_at=? WHERE id=?',
                    (json.dumps(state), '2026-01-02T00:00:00+00:00', row['id']))
    runtime.store.start_jobs([job_id], 'local')
    latest = finish_regular(runtime)
    assert latest['id'] == job_id and latest['attempt'] == first['attempt'] + 1
    local = _save_local_timeline(runtime, latest, offset=172800)
    detail = runtime.store.database.latest_recognition_calls(latest['result_id'])
    assert detail['incremental'] is False
    assert not detail['calls'][0].get('_run', {}).get('retained')
    assert all(run['kind'] == 'current' for run in detail['timeline']['runs'])
    assert detail['timeline']['elapsed_seconds'] == 6
    assert len(detail['calls']) == 1
    with runtime.store.database.connect() as con:
        remote = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job_id,)).fetchone()
        polluted = merge_remote_snapshot(local, remote_call(dict(remote), latest['filename']))
        assert polluted['processing_seconds'] > 86400
        con.execute('UPDATE latest_recognition_calls SET snapshot_json=? WHERE filename=?',
                    (json.dumps(polluted), latest['filename']))
    repaired = runtime.store.database.latest_recognition_calls(latest['result_id'])
    assert repaired['processing_seconds'] == repaired['timeline']['elapsed_seconds'] == 6
    assert len(repaired['calls']) == 1


def test_pending_remote_continuation_is_kept_when_a_new_local_attempt_finishes(runtime):
    job_id = start(runtime, mixed=True)
    first = finish_regular(runtime)
    runtime.store.remote_action([job_id], 'resubmit')
    _save_remote_timeline(runtime, job_id)
    with runtime.store.database.connect() as con:
        row = con.execute('SELECT id,recognition_call_json,seal_requests_json FROM danzhengtong_jobs WHERE job_id=? AND active=1',
                          (job_id,)).fetchone()
        call, state = json.loads(row['recognition_call_json']), json.loads(row['seal_requests_json'])
        for key in ('finished_at', 'result_received_at', 'applied_at'):
            call['transport'].pop(key)
        state['page']['status'] = 'waiting'
        con.execute("UPDATE danzhengtong_jobs SET status='waiting',recognition_call_json=?,seal_requests_json=?,created_at=? WHERE id=?",
                    (json.dumps(call), json.dumps(state), '2026-01-02T00:00:00+00:00', row['id']))
    runtime.store.start_jobs([job_id], 'local')
    latest = finish_regular(runtime)
    _save_local_timeline(runtime, latest, offset=172800)
    detail = runtime.store.database.latest_recognition_calls(latest['result_id'])
    assert len(detail['calls']) == 2
    assert detail['timeline']['elapsed_seconds'] is None
    assert any(span.get('method') == 'danzhengtong' for run in detail['timeline']['runs'] for span in run['spans'])


def test_incomplete_local_or_remote_completion_cannot_claim_finished_wall_time():
    local = timeline_local()
    local['finished_at'] = ''
    local['processing_seconds'] = None
    local['status'] = 'running'
    pending = merge_remote_snapshot(local, timeline_remote())
    assert pending['processing_seconds'] is None and pending['finished_at'] == ''
    remote = timeline_remote(status='received')
    remote['transport'].pop('applied_at')
    received = merge_remote_snapshot(timeline_local(), remote)
    assert received['processing_seconds'] is None and received['finished_at'] == ''
    remote['_remote_status'] = 'applied'  # Historical apply has no exact saved-completion timestamp.
    historical = merge_remote_snapshot(timeline_local(), remote)
    assert historical['processing_seconds'] == 13
    assert historical['processing_finished_scope'] == 'result_received'
    assert historical['processing_seconds_approximate'] is True


def test_legacy_regular_end_can_be_derived_but_never_from_an_old_combined_remote_sum():
    local = timeline_local()
    local.pop('finished_at')
    merged = merge_remote_snapshot(local, timeline_remote())
    assert merged['processing_seconds'] == 14
    assert merged['_regular_timing']['finished_at_approximate']
    assert merged['processing_seconds_approximate']
    local['calls'].append({'method': 'danzhengtong', 'status': 'succeeded', 'seconds': 2})
    unknown = merge_remote_snapshot(local, timeline_remote())
    assert unknown['processing_seconds'] is None
    assert unknown['_regular_timing']['finished_at'] == ''


def test_failed_terminal_remote_uses_measured_failure_boundary_without_result_label():
    remote = timeline_remote(status='failed')
    remote['status'] = 'failed'
    remote['transport'].pop('applied_at')
    remote['transport'].pop('result_received_at')
    merged = merge_remote_snapshot(timeline_local(), remote)
    assert merged['status'] == 'failed' and merged['processing_seconds'] == 13
    assert merged['processing_finished_scope'] == 'remote_failed'
    assert merged['processing_seconds_approximate'] is False


def test_applied_timestamp_rolls_back_with_a_failed_combined_save(timed, monkeypatch):
    service, provider, clock = timed
    job_id = start(service)
    service.worker.step()
    finish_regular(service)
    provider.ready = True
    query(service, job_id)
    received = saved_call(service, job_id)
    clock.seconds += 10
    task = service.store.claim_remote()
    original = service.store.database.sync_remote_recognition_calls

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('save transaction failed')

    monkeypatch.setattr(service.store.database, 'sync_remote_recognition_calls', fail)
    with pytest.raises(sqlite3.OperationalError):
        service.worker._apply(task)
    assert saved_call(service, job_id) == received
    assert service.store.remote_detail(job_id)['status'] == 'received'
    monkeypatch.setattr(service.store.database, 'sync_remote_recognition_calls', original)
    service.worker._apply(task)
    assert saved_call(service, job_id)['transport']['applied_at'] == '2026-01-01T00:00:19.000000+00:00'
