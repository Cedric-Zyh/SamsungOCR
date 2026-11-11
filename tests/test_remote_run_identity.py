"""New attempts use explicit provenance; legacy projection remains conservative."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.domain.remote_runs import LocalAttempt, RemoteRunLink
from receipt_ocr.persistence.remote_trace_projection import RemoteTraceInput, project_remote_trace
from test_danzhengtong_jobs import runtime, start, finish_regular
from test_async_remote_timings import timeline_local, timeline_remote


def saved_remote(runtime, job_id):
    with runtime.store.database.connect() as con:
        row = dict(con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=? AND active=1', (job_id,)).fetchone())
    return row, json.loads(row['seal_requests_json'])


def test_initial_remote_attempt_binds_to_the_upcoming_local_claim(runtime):
    job_id = start(runtime, mixed=True)
    remote, state = saved_remote(runtime, job_id)
    link = RemoteRunLink.from_json(state['run_link'])
    assert link.created_for == LocalAttempt(job_id, 1)
    assert link.local_evidence == 'shared' and link.remote_id == remote['id']
    job = finish_regular(runtime)
    assert RemoteRunLink.from_json(saved_remote(runtime, job_id)[1]['run_link']).attached_to == LocalAttempt(job_id, job['attempt'])


def test_remote_only_initial_run_keeps_its_actual_unclaimed_attempt(runtime):
    job_id = start(runtime, mode='danzhengtong')
    _, state = saved_remote(runtime, job_id)
    assert RemoteRunLink.from_json(state['run_link']).attached_to == LocalAttempt(job_id, 0)


def test_remote_resubmission_has_a_distinct_identity_and_explicit_local_reuse(runtime):
    job_id = start(runtime, mixed=True)
    job = finish_regular(runtime)
    previous, _ = saved_remote(runtime, job_id)
    runtime.store.remote_action([job_id], 'resubmit')
    remote, state = saved_remote(runtime, job_id)
    link = RemoteRunLink.from_json(state['run_link'])
    assert link.remote_id != previous['id'] and link.remote_id == remote['id']
    assert link.replaces_remote_id == previous['id']
    assert link.attached_to == LocalAttempt(job_id, job['attempt'])
    assert link.local_evidence == 'retained'


def test_inflight_worker_cannot_revert_a_new_local_attempt_link(runtime):
    job_id = start(runtime, mixed=True)
    claimed = runtime.store.claim_remote()
    old_state = json.loads(claimed['seal_requests_json'])
    first = finish_regular(runtime)
    runtime.store.start_jobs([job_id], 'local')
    latest = runtime.store.claim()
    assert latest['attempt'] == first['attempt'] + 1
    old_state['page']['status'] = 'uploading'
    assert runtime.store.update_remote(claimed, seal_requests_json=json.dumps(old_state))
    _, current = saved_remote(runtime, job_id)
    link = RemoteRunLink.from_json(current['run_link'])
    assert link.attached_to == LocalAttempt(job_id, latest['attempt'])
    assert link.remote_evidence == 'continuing' and current['page']['status'] == 'uploading'


def trace_input(link):
    local = timeline_local()
    state = {'page': {'status': 'completed'}, 'requests': [], 'run_link': link.to_json()}
    remote = dict(id='remote', job_id='job', attempt=2, job_status='succeeded', status='applied',
                  req_uuid='accepted', recognition_call_json=json.dumps(timeline_remote()),
                  seal_requests_json=json.dumps(state), result_json='{}',
                  payload_json=json.dumps({'recognition_config': {'fields': ['paddle_v6'], 'seal': ['danzhengtong']}}),
                  submitted_at='2026-01-01T00:00:01+00:00', last_query_at='2026-01-01T00:00:13+00:00',
                  created_at='2099-01-01T00:00:00+00:00', job_created_at='1990-01-01T00:00:00+00:00',
                  job_finished_at='', remote_rowid=1)
    current = {'job_id': 'job', 'attempt': 2, 'started_at': local['started_at'], 'snapshot_json': json.dumps(local)}
    return RemoteTraceInput(remote, current, has_predecessor=True)


@pytest.mark.parametrize('retains_local,expected', [(False, 14), (True, 13)])
def test_explicit_link_ignores_legacy_row_and_timestamp_heuristics(monkeypatch, retains_local, expected):
    link = RemoteRunLink.create('remote', LocalAttempt('job', 2), retains_local=retains_local)
    evidence = trace_input(link)
    before = deepcopy(evidence)
    def unexpected(*args, **kwargs):
        pytest.fail('new run links must not infer ownership from legacy chronology')
    monkeypatch.setattr('receipt_ocr.persistence.remote_trace_projection.retains_regular_run', unexpected)
    monkeypatch.setattr('receipt_ocr.persistence.remote_trace_projection.newer_local_trace', unexpected)
    snapshot = project_remote_trace(evidence, 'receipt.jpg')
    assert snapshot['processing_seconds'] == expected
    assert snapshot['incremental'] is retains_local
    assert evidence == before


def test_reused_completed_remote_result_never_becomes_a_call_in_the_new_local_run():
    link = RemoteRunLink.create('remote', LocalAttempt('job', 1)).attach(LocalAttempt('job', 2), completed=True)
    snapshot = project_remote_trace(trace_input(link), 'receipt.jpg')
    assert snapshot['processing_seconds'] == 6
    assert [call['method'] for call in snapshot['calls']] == ['paddle_v6']
    assert not snapshot['incremental']


def test_unknown_identity_version_uses_saved_legacy_provenance():
    evidence = trace_input(RemoteRunLink.create('remote', LocalAttempt('job', 2)))
    state = json.loads(evidence.remote['seal_requests_json'])
    state['run_link']['version'] = 99
    state.update(retained_regular_job_id='job', retained_regular_attempt=2)
    evidence.remote['seal_requests_json'] = json.dumps(state)
    assert project_remote_trace(evidence, 'receipt.jpg')['incremental'] is True
