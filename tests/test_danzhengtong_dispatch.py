"""Scheduling must scale with due work, without weakening leases or fairness."""
from contextlib import contextmanager
import json
import threading

from receipt_ocr.jobs.danzhengtong_schedule import refresh_dirty
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from test_danzhengtong_jobs import runtime, start


def set_page(runtime, job_id, status, *, due='', parent_status=None):
    state = {'version': 2, 'prepared': True, 'seal_required': False, 'page_required': True,
             'requests': [], 'page': {'status': status, 'next_query_at': due, 'error': ''}}
    with runtime.store.database.connect() as con:
        con.execute('''UPDATE danzhengtong_jobs SET status=?,req_uuid=?,seal_requests_json=?,
            next_query_at=?,lease_until='' WHERE job_id=?''',
            (parent_status or status, 'request-' + job_id if status == 'waiting' else '',
             json.dumps(state), due, job_id))


def test_idle_polls_do_not_decode_future_or_terminal_evidence_or_take_write_lock(runtime, monkeypatch):
    future = start(runtime, task_id='future')
    failed = start(runtime, task_id='failed')
    unknown = start(runtime, task_id='unknown')
    set_page(runtime, future, 'waiting', due='2099-01-01T00:00:00+08:00')
    set_page(runtime, failed, 'failed')
    set_page(runtime, unknown, 'unknown')
    refresh_dirty(runtime.store.database)

    def no_evidence_decode(*args, **kwargs):
        raise AssertionError('Idle polling must not reload provider evidence')

    monkeypatch.setattr('receipt_ocr.jobs.danzhengtong_store.remote_state', no_evidence_decode)
    statements = []
    original = runtime.store.database.connect

    @contextmanager
    def traced():
        with original() as con:
            con.set_trace_callback(statements.append)
            yield con

    monkeypatch.setattr(runtime.store.database, 'connect', traced)
    for _ in range(3):
        assert runtime.store.claim_remote() is None
    assert not any(sql.lstrip().upper().startswith(('BEGIN IMMEDIATE', 'UPDATE', 'INSERT')) for sql in statements)


def test_submit_and_due_query_lanes_alternate_without_starvation(runtime):
    waiting = start(runtime, task_id='waiting')
    pending = start(runtime, task_id='pending')
    set_page(runtime, waiting, 'waiting')
    set_page(runtime, pending, 'queued')
    first = runtime.store.claim_remote()
    assert first['job_id'] == waiting and first['_operation'] == 'page_query'
    runtime.store.update_remote(first, lease_until='')
    # The old query remains due. The next slot must still permit a new POST.
    second = runtime.store.claim_remote()
    assert second['job_id'] == pending and second['_operation'] == 'page_submit'
    runtime.store.update_remote(second, lease_until='')
    third = runtime.store.claim_remote()
    assert third['job_id'] == waiting and third['_operation'] == 'page_query'


def test_changed_child_deadline_is_indexed_even_when_parent_backoff_is_future(runtime):
    job_id = start(runtime)
    set_page(runtime, job_id, 'waiting', due='2099-01-01T00:00:00+08:00')
    assert runtime.store.claim_remote() is None
    with runtime.store.database.connect() as con:
        row = con.execute('SELECT seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(row[0])
        state['requests'] = [{'status': 'waiting', 'req_uuid': 'stamp-0', 'next_query_at': ''}]
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE job_id=?', (json.dumps(state), job_id))
    claimed = runtime.store.claim_remote()
    assert claimed['_operation'] == 'seal_query' and claimed['_child_index'] == 0


def test_new_pause_during_decode_prevents_stale_submission_claim(runtime, monkeypatch):
    from receipt_ocr.jobs import danzhengtong_schedule as schedule
    job_id = start(runtime)
    set_page(runtime, job_id, 'queued')
    refresh_dirty(runtime.store.database)
    original = schedule.normalized_task
    changed = []

    def pause_while_decoding(task):
        if 'regular_status' in task and not changed:
            changed.append(True)
            # A second connection can write here: expensive decoding owns no lock.
            runtime.store.remote_control({'submit_paused': True})
        return original(task)

    monkeypatch.setattr(schedule, 'normalized_task', pause_while_decoding)
    assert runtime.store.claim_remote() is None
    assert changed
    runtime.store.remote_control({'submit_paused': False})
    assert runtime.store.claim_remote()['_operation'] == 'page_submit'


def test_new_acceptance_during_decode_restarts_claim_as_query(runtime, monkeypatch):
    from receipt_ocr.jobs import danzhengtong_schedule as schedule
    job_id = start(runtime)
    set_page(runtime, job_id, 'queued')
    refresh_dirty(runtime.store.database)
    original = schedule.normalized_task
    changed = []

    def accept_while_decoding(task):
        if 'regular_status' in task and not changed:
            changed.append(True)
            set_page(runtime, job_id, 'waiting')
        return original(task)

    monkeypatch.setattr(schedule, 'normalized_task', accept_while_decoding)
    claimed = runtime.store.claim_remote()
    assert claimed['_operation'] == 'page_query'
    assert claimed['req_uuid'] == 'request-' + job_id


def test_two_claimants_cannot_receive_the_same_parent_lease(runtime, monkeypatch):
    from receipt_ocr.jobs import danzhengtong_schedule as schedule
    job_id = start(runtime)
    set_page(runtime, job_id, 'queued')
    refresh_dirty(runtime.store.database)
    original = schedule.normalized_task
    barrier = threading.Barrier(2)

    def simultaneous_decode(task):
        if 'regular_status' in task:
            barrier.wait(timeout=5)
        return original(task)

    monkeypatch.setattr(schedule, 'normalized_task', simultaneous_decode)
    claimed, errors = [], []

    def run():
        try:
            store = JobStore(Database(runtime.store.database.path))
            claimed.append(store.claim_remote())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert len([item for item in claimed if item is not None]) == 1
    assert next(item for item in claimed if item)['job_id'] == job_id


def test_manual_query_bypasses_future_deadline_and_paused_controls(runtime):
    job_id = start(runtime)
    set_page(runtime, job_id, 'waiting', due='2099-01-01T00:00:00+08:00')
    runtime.store.remote_control({'submit_paused': True, 'auto_query': False})
    assert runtime.store.claim_remote() is None
    runtime.store.remote_action([job_id], 'query')
    claimed = runtime.store.claim_remote()
    assert claimed['_operation'] == 'page_query' and claimed['_manual_query']


def test_existing_database_adds_schedule_without_resubmitting_accepted_requests(runtime):
    job_id = start(runtime)
    set_page(runtime, job_id, 'waiting')
    # Remove only the new projection to recreate the preceding database schema.
    with runtime.store.database.connect() as con:
        con.execute("DELETE FROM schema_migrations WHERE component='danzhengtong_jobs'")
        con.execute('DROP TRIGGER invalidate_dzt_dispatch')
        for kind in ('dirty', 'submit', 'query', 'apply', 'manual'):
            con.execute(f'DROP INDEX idx_dzt_dispatch_{kind}')
        for column in ('dispatch_version', 'dispatch_dirty', 'dispatch_submit', 'dispatch_query_due', 'dispatch_apply_due'):
            con.execute(f'ALTER TABLE danzhengtong_jobs DROP COLUMN {column}')
    reopened = JobStore(Database(runtime.store.database.path))
    reopened.initialize()
    claimed = reopened.claim_remote()
    assert claimed['_operation'] == 'page_query'
    assert claimed['job_id'] == job_id and claimed['req_uuid'] == 'request-' + job_id
