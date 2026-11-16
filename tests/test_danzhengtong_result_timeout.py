"""A missing remote result ends its own wait without another provider call."""
from datetime import datetime, timedelta
import json

import pytest

from receipt_ocr.domain.remote_tasks import RESULT_WAIT_TIMEOUT_ERROR
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from test_danzhengtong_jobs import runtime, start, finish_regular, query
from test_danzhengtong_seal_jobs import stamps, start_seal_only


def old_submission():
    return (datetime.now().astimezone() - timedelta(seconds=301)).isoformat(timespec='seconds')


def expire_page(service, job_id):
    with service.store.database.connect() as con:
        row = con.execute('SELECT seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(row[0])
        state.update(prepared=True, seal_required=False, requests=[])
        state['page'].update(status='waiting', next_query_at='2099-01-01T00:00:00+08:00')
        con.execute('UPDATE danzhengtong_jobs SET submitted_at=?,seal_requests_json=?,next_query_at=? WHERE job_id=?',
                    (old_submission(), json.dumps(state), '2099-01-01T00:00:00+08:00', job_id))


@pytest.mark.parametrize('remote_only', [False, True])
def test_overdue_page_fails_the_run_and_stays_failed_after_restart(runtime, remote_only):
    job_id = start(runtime, mixed=not remote_only, mode='danzhengtong' if remote_only else 'all')
    assert runtime.worker.step()
    if not remote_only:
        finish_regular(runtime)
    expire_page(runtime, job_id)
    assert runtime.worker.step()  # The deadline wins over a future backoff.
    assert runtime.client.queries == 0
    assert runtime.store.remote_detail(job_id)['seal_result']['page']['status'] == 'failed'
    assert runtime.worker.step()  # Persist a terminal result without rereading the image.
    detail = runtime.store.remote_detail(job_id)
    job = runtime.store.get(job_id)
    assert detail['status'] == job['status'] == 'failed'
    assert detail['pending'] is False and detail['queryable'] is False
    assert detail['req_uuid'] == 'original-request'
    assert job['error_message'] == RESULT_WAIT_TIMEOUT_ERROR
    assert runtime.store.database.get_result(job['result_id'])['overall'] == '识别失败'
    reopened = JobStore(Database(runtime.store.database.path))
    reopened.initialize()
    reopened.recover_remote()
    assert reopened.remote_detail(job_id)['status'] == 'failed'
    assert reopened.claim_remote() is None
    assert (runtime.client.uploads, runtime.client.submits, runtime.client.queries) == (1, 1, 0)


def test_one_timed_out_stamp_keeps_another_completed_stamps_result(stamps):
    job_id = start_seal_only(stamps)
    assert stamps.worker.step() and stamps.worker.step()
    query(stamps, job_id)  # One successful stamp is durable before the other times out.
    with stamps.store.database.connect() as con:
        row = con.execute('SELECT seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(row[0])
        completed = next(child for child in state['requests'] if child['status'] == 'completed')
        completed_result = completed['result']
        waiting = next(child for child in state['requests'] if child['status'] == 'waiting')
        waiting.update(submitted_at=old_submission(), next_query_at='2099-01-01T00:00:00+08:00')
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE job_id=?', (json.dumps(state), job_id))
    queries_before = list(stamps.stamp_state['queries'])
    assert stamps.worker.step() and stamps.worker.step()
    detail = stamps.store.remote_detail(job_id)
    assert detail['status'] == 'failed'
    assert stamps.store.get(job_id)['status'] == 'failed'
    assert stamps.stamp_state['queries'] == queries_before
    assert next(child for child in detail['seal_result']['requests'] if child['status'] == 'completed')['result'] == completed_result
    expired = next(child for child in detail['seal_result']['requests'] if child['status'] == 'failed')
    assert expired['failure_code'] == 'result_timeout' and expired['req_uuid']
    assert expired['query_count'] == 0


def test_a_stamp_timeout_fails_a_mixed_run_without_erasing_the_completed_page(stamps):
    job_id = start(stamps, mixed=True)
    assert stamps.worker.step() and stamps.worker.step() and stamps.worker.step()
    regular = finish_regular(stamps)
    stamps.client.ready = True
    query(stamps, job_id)
    query(stamps, job_id)
    with stamps.store.database.connect() as con:
        row = con.execute('SELECT result_json,seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        page = row['result_json']
        state = json.loads(row['seal_requests_json'])
        child = next(item for item in state['requests'] if item['status'] == 'waiting')
        child.update(submitted_at=old_submission(), next_query_at='')
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE job_id=?', (json.dumps(state), job_id))
    assert stamps.worker.step() and stamps.worker.step()
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['overall'] == '识别失败'
    assert result['fields']['客户名称'] == '测试客户'
    assert result['date_check']['actual'] == '2025-10-27'
    assert stamps.store.get(job_id)['status'] == stamps.store.remote_detail(job_id)['status'] == 'failed'
    with stamps.store.database.connect() as con:
        assert con.execute('SELECT result_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()[0] == page


def test_manual_query_does_not_reset_the_five_minute_limit(runtime):
    job_id = start(runtime, mode='danzhengtong')
    assert runtime.worker.step()
    expire_page(runtime, job_id)
    runtime.store.remote_control({'auto_query': False, 'submit_paused': True})
    query(runtime, job_id)
    assert runtime.client.queries == 0
    assert runtime.worker.step()
    assert runtime.store.get(job_id)['status'] == 'failed'


def test_an_empty_response_crossing_the_deadline_ends_the_wait_after_that_query(runtime, monkeypatch):
    job_id = start(runtime, mode='danzhengtong')
    assert runtime.worker.step()
    submitted = datetime.now().astimezone() - timedelta(seconds=299)
    clock = [submitted + timedelta(seconds=299)]
    monkeypatch.setattr('receipt_ocr.jobs.danzhengtong_worker.now_iso', lambda: clock[0].isoformat(timespec='seconds'))
    with runtime.store.database.connect() as con:
        con.execute('UPDATE danzhengtong_jobs SET submitted_at=? WHERE job_id=?', (submitted.isoformat(timespec='seconds'), job_id))
    request = runtime.client.get_result
    def empty(*args, **kwargs):
        clock[0] += timedelta(seconds=2)
        return request(*args, **kwargs)
    monkeypatch.setattr(runtime.client, 'get_result', empty)
    query(runtime, job_id)
    assert runtime.client.queries == 1
    assert runtime.store.remote_detail(job_id)['seal_result']['page']['failure_code'] == 'result_timeout'
    assert runtime.worker.step()
    assert runtime.store.get(job_id)['status'] == 'failed'


def test_only_an_explicit_resubmission_starts_a_new_wait_after_timeout(runtime):
    job_id = start(runtime, mode='danzhengtong')
    assert runtime.worker.step()
    expire_page(runtime, job_id)
    assert runtime.worker.step() and runtime.worker.step()
    runtime.store.remote_action([job_id], 'query')
    assert not runtime.worker.step()
    assert runtime.client.submits == 1
    old_id = runtime.store.remote_detail(job_id)['id']
    runtime.store.remote_action([job_id], 'resubmit')
    assert runtime.worker.step()
    current = runtime.store.remote_detail(job_id)
    assert current['id'] != old_id and current['status'] == 'waiting'
    assert not current['seal_result'].get('timeout_applied')
    assert not current['seal_result']['page'].get('failure_code')
    assert runtime.client.submits == 2
