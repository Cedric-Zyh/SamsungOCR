"""Retention and queue admission cannot invalidate each other's protection checks."""
from contextlib import contextmanager
from datetime import datetime
import json
import threading

import pytest

from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from test_database import sample_result


NOW = datetime.fromisoformat('2026-11-10T12:00:00+08:00')
OPTIONS = {'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'local'}


@pytest.fixture
def old_receipt(tmp_path):
    database = Database(tmp_path / 'retention.db')
    database.initialize()
    store = JobStore(database)
    store.initialize()
    item = {**sample_result(), 'created_at': '2020-01-01T12:00:00+08:00'}
    result_id = database.insert_result(filename=item['filename'], stored_name='old.jpg',
                                      preview_name='', task_id='', result=item)
    return database, store, result_id


def test_retry_admitted_before_retention_protects_its_target(old_receipt):
    database, store, result_id = old_receipt
    store.create_retries('retry-first', [result_id], {result_id: OPTIONS})
    assert database.purge_expired(30, now=NOW)['deleted_results'] == 0
    assert database.get_result(result_id)['id'] == result_id


def test_retention_does_not_allow_retry_between_protection_check_and_delete(old_receipt, monkeypatch):
    database, _, result_id = old_receipt
    competing = JobStore(Database(database.path))
    start, attempting, finished = threading.Event(), threading.Event(), threading.Event()
    outcome = []

    def admit():
        assert start.wait(5)
        attempting.set()
        try:
            competing.create_retries('racing-retry', [result_id], {result_id: OPTIONS})
            outcome.append('admitted')
        except KeyError:
            outcome.append('deleted')
        finally:
            finished.set()

    original = database.connect
    premature = []

    class InterleavingConnection:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, parameters=()):
            # The active-retry collection has been fully read at this point.
            if sql == "SELECT 1 FROM sqlite_master WHERE name='danzhengtong_jobs'":
                start.set()
                assert attempting.wait(5)
                premature.append(finished.wait(.15))
            return self.con.execute(sql, parameters)

    @contextmanager
    def connect():
        with original() as con:
            yield InterleavingConnection(con)

    monkeypatch.setattr(database, 'connect', connect)
    thread = threading.Thread(target=admit)
    thread.start()
    try:
        assert database.purge_expired(30, now=NOW)['deleted_results'] == 1
        assert finished.wait(5)
    finally:
        start.set()
        thread.join(5)
    assert premature == [False]
    assert outcome == ['deleted']
    with original() as con:
        assert con.execute("SELECT COUNT(*) FROM recognition_jobs WHERE target_result_id=?", (result_id,)).fetchone()[0] == 0


@pytest.mark.parametrize('download_status,kept', [('queued', True), ('running', True), ('completed', False)])
def test_retention_keeps_remote_image_downloads_until_finished(old_receipt, download_status, kept):
    database, store, result_id = old_receipt
    task = store.create_retries('download', [result_id], {result_id: OPTIONS})
    job_id = task['items'][0]['id']
    with database.connect() as con:
        con.execute("UPDATE recognition_jobs SET status='succeeded',result_id=?,created_at='2020-01-01' WHERE id=?",
                    (result_id, job_id))
        con.execute('''INSERT INTO danzhengtong_jobs
            (id,job_id,status,created_at,updated_at,result_id,seal_requests_json)
            VALUES('remote',?,'applied','2020-01-01','2020-01-01',?,?)''',
            (job_id, result_id, json.dumps({'requests': [{'image_download': {'status': download_status}}]})))
    report = database.purge_expired(30, now=NOW)
    assert report['deleted_results'] == int(not kept)
    assert report['deleted_jobs'] == int(not kept)
    if kept:
        assert database.get_result(result_id)['id'] == result_id
