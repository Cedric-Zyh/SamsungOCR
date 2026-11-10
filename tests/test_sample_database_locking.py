"""Sample preservation must not block on its own SQLite write transaction."""
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3

import pytest

from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from receipt_ocr.samples.execution import sample_execution_options
from receipt_ocr.samples.preservation import restore_previous_sample_results
from sample_support import SampleCenter
from test_sample_center import samples
from test_sample_provider_preservation import legacy_result


def seed_history(service, *, large=False):
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    options = {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
               'recognition_config': settings['recognition_config']}
    service.store.enqueue(['one.png', 'two.png'], options, settings)
    for _ in range(2):
        job = service.store.claim()
        result = legacy_result()
        if large:
            result['raw_text'] = 'saved OCR evidence ' * 10000
        service.store.finish(job, result, 'old.jpg')
    return settings


def seed_remote_runs(service, settings):
    plan = {stage: ['danzhengtong'] for stage in ('fields', 'handwriting', 'date', 'seal')}
    plan['products'] = []
    # This older format predates preservation; it already has durable remote requests.
    service.store.enqueue(['one.png', 'two.png'],
                          {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
                           'recognition_config': plan}, settings)
    job_ids = []
    for _ in range(2):
        job = service.store.claim()
        service.store.finish(job, {'fields': {'客户名称': 'remote'}, 'recognition_config': plan})
        job_ids.append(job['id'])
        with service.database.connect() as con:
            payload = json.loads(job['payload_json'])
            payload['_sample_execution_mode'] = 'danzhengtong'
            con.execute('UPDATE recognition_jobs SET payload_json=? WHERE id=?',
                        (json.dumps(payload), job['id']))
            con.execute("UPDATE danzhengtong_jobs SET status='waiting',req_uuid=? WHERE job_id=?",
                        ('saved-' + job['filename'], job['id']))
    return job_ids


def short_connections(monkeypatch, database, *, small_cache=False, after_commit=None):
    original = database.connect

    @contextmanager
    def connect():
        with original() as con:
            con.execute('PRAGMA busy_timeout=50')
            if small_cache:
                con.execute('PRAGMA cache_size=5')
            yield con
            changed = con.total_changes
        if changed and after_commit is not None:
            after_commit()

    monkeypatch.setattr(database, 'connect', connect)


def test_source_uses_uncommitted_mapping_without_releasing_the_callers_lock(samples):
    ctx, _ = samples
    service = SampleCenter(ctx)
    storage = service.library.directory / 'managed.png'
    storage.write_bytes((ctx.DATA_DIR / 'one.png').read_bytes())
    with service.database.connect() as con:
        con.execute('BEGIN EXCLUSIVE')
        con.execute('INSERT INTO sample_library(filename,storage_name) VALUES(?,?)',
                    ('new.png', storage.name))
        assert service.library.source('new.png', connection=con) == storage.resolve()
        con.execute("UPDATE sample_library SET deleted_at='deleted' WHERE filename='new.png'")
        with pytest.raises(KeyError):
            service.library.source('new.png', connection=con)
        assert service.library.source('new.png', include_deleted=True, connection=con) == storage.resolve()
        for name in ('../outside.png', 'symlink.png'):
            with pytest.raises(KeyError):
                service.library.source(name, connection=con)
        # Reading the source must not commit or close the caller's transaction.
        assert con.in_transaction
        con.rollback()
    with pytest.raises(KeyError):
        service.library.source('new.png')


def test_large_remote_only_batch_preserves_both_samples_without_self_locking(samples, monkeypatch):
    ctx, _ = samples
    service = SampleCenter(ctx)
    settings = seed_history(service, large=True)
    with service.database.connect() as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    short_connections(monkeypatch, service.database, small_cache=True)
    options = sample_execution_options(settings, 'danzhengtong', api_enabled=False)
    submitted = service.store.enqueue(['one.png', 'two.png'], options, settings)
    assert submitted['accepted'] == ['one.png', 'two.png']
    items = {item['filename']: item for item in service.listing()['items']}
    for name in submitted['accepted']:
        item = items[name]
        assert item['retained_results'] and item['status'] == '测试中'
        assert service.database.get_result(item['result_id'])['raw_text'] == 'saved OCR evidence ' * 10000
        assert service.store.remote_detail(item['job_id'])['status'] == 'queued'


def test_restore_commits_each_sample_and_keeps_saved_remote_requests(samples, monkeypatch):
    ctx, _ = samples
    service = SampleCenter(ctx)
    job_ids = seed_remote_runs(service, seed_history(service))
    observed = []

    def observe_commit():
        with sqlite3.connect(service.database.path, timeout=.1) as reader:
            observed.append(reader.execute("""SELECT COUNT(*) FROM recognition_jobs
                WHERE json_extract(payload_json,'$._provider_preservation_checked')=1""").fetchone()[0])
            # Another request can also acquire a writer between repaired samples.
            reader.execute("UPDATE queue_control SET updated_at='between repairs' WHERE id=1")

    short_connections(monkeypatch, service.database, after_commit=observe_commit)
    restore_previous_sample_results(service.store)
    assert observed == [1, 2]
    for job_id in job_ids:
        job = service.store.get(job_id)
        result = service.database.get_result(job['result_id'])
        assert result['raw_text'] == '本地原文证据'
        remote = service.store.remote_detail(job_id)
        assert remote['status'] == 'waiting'
        assert remote['req_uuid'] == 'saved-' + job['filename']
    restore_previous_sample_results(service.store)
    assert observed == [1, 2]


@pytest.mark.parametrize('change', ['cancelled', 'superseded', 'deleted_result'])
def test_restore_rechecks_jobs_changed_after_the_initial_scan(samples, monkeypatch, change):
    ctx, _ = samples
    service = SampleCenter(ctx)
    target_id = seed_remote_runs(service, seed_history(service))[1]
    target = service.store.get(target_id)
    original = service.database.connect
    scanned = False

    @contextmanager
    def connect():
        nonlocal scanned
        with original() as con:
            con.execute('PRAGMA busy_timeout=50')
            yield con
        if not scanned:
            scanned = True
            # Another request changes the candidate after the list was read.
            with sqlite3.connect(service.database.path, timeout=.1) as writer:
                if change == 'cancelled':
                    writer.execute("UPDATE recognition_jobs SET status='cancelled' WHERE id=?", (target_id,))
                elif change == 'deleted_result':
                    writer.execute("UPDATE results SET deleted_at='deleted' WHERE id=?", (target['result_id'],))
                else:
                    writer.execute('''INSERT INTO recognition_jobs
                        (id,task_id,client_key,filename,stored_name,status,payload_json,created_at)
                        VALUES('newer',?,'newer',?,?,'cancelled',?,?)''',
                        (target['task_id'], target['filename'], target['stored_name'],
                         target['payload_json'], target['created_at']))

    monkeypatch.setattr(service.database, 'connect', connect)
    restore_previous_sample_results(service.store)
    current = service.store.get(target_id)
    assert not json.loads(current['payload_json']).get('_provider_preservation_checked')
    with original() as con:
        result = json.loads(con.execute('SELECT result_json FROM results WHERE id=?',
                                        (target['result_id'],)).fetchone()[0])
    assert result['fields']['客户名称'] == 'remote'
