"""Page and stamp continuations share a lease, never a success dependency."""
import json

import pytest

from test_danzhengtong_jobs import runtime, start, finish_regular, query
from test_danzhengtong_seal_jobs import stamps, start_seal_only, seed_prepared_requests, complete


def edit_state(service, job_id, change):
    with service.store.database.connect() as con:
        row = con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(row['seal_requests_json'])
        change(state)
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                    (json.dumps(state), row['id']))


def test_both_scenes_are_accepted_before_either_result_is_available(stamps):
    job_id = start(stamps, mixed=True)
    assert stamps.worker.step() and stamps.worker.step() and stamps.worker.step()
    detail = stamps.store.remote_detail(job_id)
    assert detail['req_uuid'] == 'original-request' and detail['result'] == {}
    assert detail['seal_result']['page']['status'] == 'waiting'
    assert all(child['req_uuid'] and child['status'] == 'waiting' for child in detail['seal_result']['requests'])
    assert stamps.client.queries == 0 and not stamps.stamp_state['queries']


def test_stamps_finish_while_page_keeps_returning_empty(stamps):
    job_id = start(stamps, mixed=True)
    for _ in range(3):
        stamps.worker.step()
    finish_regular(stamps)
    for _ in range(3):
        query(stamps, job_id)
    state = stamps.store.remote_detail(job_id)['seal_result']
    assert state['page']['status'] == 'waiting'
    assert all(child['status'] == 'completed' for child in state['requests'])
    assert stamps.store.remote_detail(job_id)['status'] == 'waiting'
    stamps.client.ready = True
    complete(stamps, job_id)
    assert stamps.store.remote_detail(job_id)['status'] == 'applied'
    assert stamps.client.submits == 1 and len(stamps.stamp_state['submissions']) == 2


def test_page_query_backoff_does_not_delay_due_stamp_query(stamps):
    job_id = start(stamps, mixed=True)
    for _ in range(3):
        stamps.worker.step()
    stamps.client.query_error = TimeoutError('page temporary timeout')
    query(stamps, job_id)
    state = stamps.store.remote_detail(job_id)['seal_result']
    page_due = state['page']['next_query_at']
    assert state['page']['error'] and page_due
    edit_state(stamps, job_id, lambda state: state['requests'][0].update(next_query_at=''))
    assert stamps.worker.step()  # Automatic polling, without overriding the page's backoff.
    state = stamps.store.remote_detail(job_id)['seal_result']
    assert stamps.client.queries == 1
    assert state['requests'][0]['status'] == 'completed'
    assert state['page']['next_query_at'] == page_due


def test_stamp_query_backoff_does_not_delay_due_page_query(stamps):
    job_id = start(stamps, mixed=True)
    for _ in range(3):
        stamps.worker.step()
    query(stamps, job_id)  # Page remains pending.
    stamps.stamp_state['query_error'].append(TimeoutError('stamp temporary timeout'))
    query(stamps, job_id)
    state = stamps.store.remote_detail(job_id)['seal_result']
    stamp_due = state['requests'][0]['next_query_at']
    assert state['requests'][0]['error'] and stamp_due
    edit_state(stamps, job_id, lambda state: state['page'].update(next_query_at=''))
    assert stamps.worker.step()
    state = stamps.store.remote_detail(job_id)['seal_result']
    assert stamps.client.queries == 2 and len(stamps.stamp_state['queries']) == 1
    assert state['requests'][0]['next_query_at'] == stamp_due


def test_preparation_failure_is_a_seal_error_but_page_result_is_preserved(stamps, monkeypatch):
    def broken(*args):
        raise ValueError('seal detector is unavailable')
    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.prepare_requests', broken)
    job_id = start(stamps, mixed=True)
    assert stamps.worker.step()
    assert stamps.client.submits == 1
    assert stamps.store.remote_detail(job_id)['seal_result']['preparation_error']
    regular = finish_regular(stamps)
    stamps.client.ready = True
    complete(stamps, job_id)
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['fields']['客户名称'] == '测试客户'
    assert result['danzhengtong']['status'] == 'completed'
    assert 'seal detector' in result['recognition_variants']['seal'][0]['error']
    assert not result['recognition_variants']['seal'][0].get('pending')
    assert not stamps.stamp_state['uploads']


def test_rejected_page_does_not_cancel_successful_stamp_branch(stamps):
    job_id = start(stamps, mixed=True)
    error = RuntimeError('page scene rejected')
    error.provider_rejected = True
    stamps.client.submit_error = error
    assert stamps.worker.step()
    regular = finish_regular(stamps)
    complete(stamps, job_id)
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['danzhengtong']['status'] == 'failed'
    assert result['seal_check']['recognized'] == '测试客户专用章'
    assert result['fields']['客户名称'] == '本地客户'
    assert stamps.client.submits == 1 and len(stamps.stamp_state['submissions']) == 2
    calls = stamps.store.database.latest_recognition_calls(regular['result_id'])['calls']
    assert next(call for call in calls if call.get('request_scope') == 'page')['status'] == 'failed'


@pytest.mark.parametrize('marker', [
    {'call': {'transport': {'phase': 'submitting'}}},
    {'call': {'transport': {'phase': 'submit_finished'}}},
    {'call': {'recognition_invoked': True}},
    {'submission_started': True},
    {'submitted_at': '2026-11-01T10:00:00+08:00'},
])
def test_stale_queued_customer_with_submit_marker_is_never_posted_again(stamps, marker):
    job_id = start_seal_only(stamps)
    seed_prepared_requests(stamps, job_id, ['客户章', '客户章'])
    edit_state(stamps, job_id, lambda state: state['requests'][0].update(marker))
    stamps.store.recover_remote()
    complete(stamps, job_id)
    requests = stamps.store.remote_detail(job_id)['seal_result']['requests']
    assert requests[0]['status'] == 'unknown'
    assert requests[1]['status'] == 'completed'
    assert stamps.stamp_state['submissions'] == ['stamp-1-input.png']


def test_legacy_child_unknown_does_not_imply_page_unknown_or_suppress_page(stamps):
    job_id = start(stamps, mixed=True)
    seed_prepared_requests(stamps, job_id, ['客户章', '客户章'])
    def legacy(state):
        state.pop('page')
        state['version'] = 1
        state['requests'][0].update(status='unknown', error='lost stamp response')
    edit_state(stamps, job_id, legacy)
    with stamps.store.database.connect() as con:
        con.execute("UPDATE danzhengtong_jobs SET status='unknown' WHERE job_id=?", (job_id,))
    stamps.store.recover_remote()
    assert stamps.store.remote_detail(job_id)['seal_result']['page']['status'] == 'queued'
    assert stamps.worker.step()
    assert stamps.client.submits == 1
    finish_regular(stamps)
    stamps.client.ready = True
    complete(stamps, job_id)
    assert stamps.stamp_state['submissions'] == ['stamp-1-input.png']
    assert stamps.client.submits == 1


def test_stale_queued_page_with_durable_submit_phase_is_not_posted_again(stamps):
    job_id = start(stamps, mixed=True)
    seed_prepared_requests(stamps, job_id, ['客户章', '客户章'])
    with stamps.store.database.connect() as con:
        con.execute('UPDATE danzhengtong_jobs SET recognition_call_json=? WHERE job_id=?',
                    (json.dumps({'transport': {'phase': 'submitting'}, 'recognition_invoked': False}), job_id))
    stamps.store.recover_remote()
    assert stamps.store.remote_detail(job_id)['seal_result']['page']['status'] == 'unknown'
    finish_regular(stamps)
    complete(stamps, job_id)
    assert stamps.client.submits == stamps.client.uploads == 0
    assert len(stamps.stamp_state['submissions']) == 2


def test_manual_query_ignores_branch_due_but_never_starts_other_branch_posts(stamps):
    job_id = start(stamps, mixed=True)
    stamps.worker.step()  # Page accepted, both stamp POSTs are still queued.
    stamps.store.remote_control({'submit_paused': True, 'auto_query': False})
    edit_state(stamps, job_id, lambda state: state['page'].update(next_query_at='2099-01-01T00:00:00+08:00'))
    stamps.store.remote_action([job_id], 'query')
    claimed = stamps.store.claim_remote()
    assert claimed['_manual_query'] and claimed['_operation'] == 'page_query'
    with stamps.store.database.connect() as con:
        assert con.execute('SELECT manual_query FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()[0] == 0
    stamps.worker._query(claimed)
    assert stamps.client.queries == 1
    assert not stamps.stamp_state['submissions']


def test_detail_keeps_real_page_uuid_separate_from_legacy_seal_uuid_fallback(stamps):
    job_id = start_seal_only(stamps)
    stamps.worker.step()
    detail = stamps.store.remote_detail(job_id)
    assert detail['page_req_uuid'] == ''
    assert detail['req_uuid'].startswith('seal-')
    assert detail['seal_result']['page_required'] is False
    assert detail['status'] == 'queued' and detail['queryable'] is True


def test_regular_retry_recombines_successful_stamps_even_when_page_never_returned(stamps):
    job_id = start(stamps, mixed=True)
    stamps.client.submit_error = TimeoutError('lost page response')
    stamps.worker.step()
    regular = finish_regular(stamps)
    complete(stamps, job_id)
    options = json.loads(regular['payload_json'])
    retry = stamps.store.create_retries('retry-partial', [regular['result_id']], {regular['result_id']: options})
    new_id = retry['items'][0]['id']
    finish_regular(stamps)
    assert stamps.worker.step()  # Recombine the saved successful seal branch only.
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['seal_check']['recognized'] == '测试客户专用章'
    assert stamps.store.remote_detail(new_id)['status'] == 'applied'
    assert stamps.client.submits == 1
    assert len(stamps.stamp_state['submissions']) == len(stamps.stamp_state['queries']) == 2
