"""Latest physical calls replace traces without removing recognition history."""
import io

from PIL import Image
import pytest

from receipt_ocr.application.document_service import DocumentRecognitionService
from receipt_ocr.jobs.worker import JobWorker
from receipt_ocr.runtime.calls import recognition_call
from receipt_ocr.web.samples import SampleCenter
from test_job_queue import store, web_queue, queued, result, wait_until
from test_sample_center import samples


OPTIONS = {'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'local', 'recognition_config': None}


def trace(*, start='2026-10-26T10:00:00+08:00', status='completed', name='seal-1.png'):
    return {'version': 1, 'started_at': start, 'finished_at': '2026-10-26T10:00:01+08:00',
            'processing_seconds': 1.0, 'status': status,
            'summary': {'total': 1, 'succeeded': int(status == 'completed'),
                        'failed': int(status == 'failed'), 'cache_hits': 2},
            'calls': [{'id': 1, 'stage': 'seal', 'stage_label': '客户印章',
                       'method': 'paddle_v6', 'method_label': 'Paddle', 'action': 'recognize',
                       'input_name': name, 'width': 120, 'height': 80,
                       'started_at': start, 'seconds': .7,
                       'status': 'failed' if status == 'failed' else 'succeeded'}]}


def save(database, filename='one.png', snapshot=None, **extra):
    payload = result()
    if snapshot is not None:
        payload['_recognition_calls'] = snapshot
    payload.update(extra)
    return database.insert_result(filename=filename, stored_name='saved.png', preview_name='',
                                  task_id='', result=payload)


def test_only_latest_trace_is_saved_outside_results_and_audit_history(store):
    db = store.database
    first = save(db, snapshot=trace())
    second = save(db, snapshot=trace(start='2026-10-26T11:00:00+08:00', name='seal-2.png'))
    assert db.latest_recognition_calls(first)['result_id'] == second
    assert db.latest_recognition_calls(first)['calls'][0]['input_name'] == 'seal-2.png'
    retry_result = result()
    retry_result['_recognition_calls'] = trace(start='2026-10-26T12:00:00+08:00', name='seal-3.png')
    db.replace_after_retry(second, retry_result, '')
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM latest_recognition_calls').fetchone()[0] == 1
        rows = con.execute('SELECT result_json,original_result_json FROM results').fetchall()
        assert len(rows) == 2
        assert all('_recognition_calls' not in value for row in rows for value in row)
        histories = con.execute('SELECT before_json,after_json FROM review_history').fetchall()
        assert len(histories) == 1
        assert all('_recognition_calls' not in value for row in histories for value in row)
    assert db.latest_recognition_calls(first)['calls'][0]['input_name'] == 'seal-3.png'
    assert '_recognition_calls' in retry_result  # A failed DB commit can retry the same input.


def test_current_run_hides_old_trace_and_late_completion_cannot_replace_it(store):
    db = store.database
    identity = save(db, snapshot=trace())
    db.begin_recognition_calls('one.png', 'old')
    db.begin_recognition_calls('one.png', 'new')
    assert not db.save_recognition_calls('one.png', trace(), result_id=identity, run_key='old')
    pending = db.latest_recognition_calls(identity)
    assert pending['status'] == 'running' and not pending['available']
    assert pending['calls'] == [] and pending['summary'] is None
    db.save_recognition_calls('one.png', trace(name='latest.png'), result_id=identity, run_key='new')
    assert db.latest_recognition_calls(identity)['calls'][0]['input_name'] == 'latest.png'


def test_new_trace_keeps_initialization_separate_from_processing_time(store):
    snapshot = {**trace(), 'model_initialization_seconds': .4, 'recognition_seconds': .6}
    identity = save(store.database, snapshot=snapshot)
    data = store.database.latest_recognition_calls(identity)
    assert data['processing_seconds'] == 1
    assert data['model_initialization_seconds'] == .4
    assert data['recognition_seconds'] == .6
    assert data['summary']['total'] == 1


def test_legacy_timing_uses_the_result_for_the_latest_successful_job(store):
    db = store.database
    old = save(db, processing_timings={'steps': {'paddle_model_setup': {'seconds': .1}}})
    snapshot = {**trace(), 'processing_seconds': 11.6288}
    latest = save(db, snapshot=snapshot,
                  queue={'job_id': 'latest-job'},
                  processing_timings={'steps': {'paddle_model_setup': {'seconds': 5.9647},
                                               'page_ocr': {'seconds': 8.872}}})
    db.begin_recognition_calls('one.png', 'latest-run', job_id='latest-job')
    db.save_recognition_calls('one.png', snapshot, result_id=latest, run_key='latest-run')
    data = db.latest_recognition_calls(old)
    assert data['model_initialization_seconds'] == 5.9647
    assert data['recognition_seconds'] == 5.6641
    assert data['processing_seconds'] == 11.6288
    # Reading the split does not rewrite historic measurements.
    assert db.get_result(latest)['processing_timings']['steps']['page_ocr']['seconds'] == 8.872


@pytest.mark.parametrize('status,job_id', [('completed', 'different-job'), ('failed', 'old-job')])
def test_legacy_trace_never_borrows_initialization_from_another_attempt(store, status, job_id):
    db = store.database
    identity = save(db, queue={'job_id': 'old-job'},
                    processing_timings={'steps': {'paddle_model_setup': {'seconds': .4}}})
    db.begin_recognition_calls('one.png', 'new-run', job_id=job_id)
    db.save_recognition_calls('one.png', trace(status=status), result_id=identity, run_key='new-run')
    data = db.latest_recognition_calls(identity)
    assert data['recognition_seconds'] is None
    assert data['model_initialization_seconds'] is None


def test_unmeasured_initialization_stays_unknown_instead_of_becoming_zero(store):
    identity = save(store.database, snapshot=trace())
    data = store.database.latest_recognition_calls(identity)
    assert data['recognition_seconds'] is None
    assert data['model_initialization_seconds'] is None


def test_unkeyed_late_result_uses_actual_start_time_and_full_filename(store):
    db = store.database
    older_id = save(db, 'folder/one.png', trace(start='2026-10-26T11:00:00+08:00'))
    latest_id = save(db, 'folder/one.png', trace(start='2026-10-26T04:00:00+00:00', name='new.png'))
    save(db, 'folder/one.png', trace(start='2026-10-26T11:30:00+08:00', name='late-old.png'))
    save(db, 'other/one.png', trace(start='2026-10-26T14:00:00+08:00', name='other.png'))
    data = db.latest_recognition_calls(older_id)
    assert data['result_id'] == latest_id and data['calls'][0]['input_name'] == 'new.png'


def test_failed_retry_preserves_result_but_replaces_latest_call_details(store):
    identity = save(store.database, snapshot=trace())
    before = store.database.get_result(identity)
    store.create_retries('retry', [identity], {identity: OPTIONS})
    # Queuing has not started a new recognition yet.
    assert store.database.latest_recognition_calls(identity)['available']
    claimed = store.claim()
    assert store.database.latest_recognition_calls(identity)['status'] == 'running'
    failed = trace(status='failed', name='failed-crop.png')
    store.finish(claimed, error='provider timeout', recognition_calls=failed)
    assert store.database.get_result(identity) == before
    data = store.database.latest_recognition_calls(identity)
    assert data['available'] and data['status'] == 'failed'
    assert data['summary']['total'] == data['summary']['failed'] == 1
    assert data['calls'][0]['input_name'] == 'failed-crop.png'


def test_interrupted_commit_retries_the_same_trace_without_recognizing_again(store, monkeypatch):
    queued(store)
    claimed = store.claim()
    payload = result()
    payload['_recognition_calls'] = trace()
    original_log = store._log
    def fail_commit(*args, **kwargs):
        raise RuntimeError('commit interrupted')
    monkeypatch.setattr(store, '_log', fail_commit)
    with pytest.raises(RuntimeError, match='commit interrupted'):
        store.finish(claimed, payload)
    assert '_recognition_calls' in payload
    assert store.database.list_results() == []
    monkeypatch.setattr(store, '_log', original_log)
    store.finish(claimed, payload)
    saved = store.database.latest_recognition_calls(store.get(claimed['id'])['result_id'])
    assert saved['available'] and saved['summary']['total'] == 1


def test_worker_keeps_partial_calls_from_exception(store):
    job = queued(store)
    def execute(claimed):
        with recognition_call('paddle_v6', 'recognize'):
            pass
        failure = RuntimeError('timeout')
        failure.recognition_calls = trace(status='failed')
        raise failure
    worker = JobWorker(store, execute)
    try:
        worker.start()
        wait_until(lambda: store.get(job['id'])['status'] == 'failed')
    finally:
        worker.stop()
    data = store.database.latest_recognition_calls(store.get(job['id'])['result_id'])
    assert data['available'] and data['summary']['failed'] == 1


def test_missing_traces_are_not_invented_from_stage_counts_or_attempts(store):
    identity = save(store.database, processing_seconds=3, processing_timings=[{'seconds': 2}],
                    queue={'attempt': 8}, attempt=5)
    data = store.database.latest_recognition_calls(identity)
    assert not data['available'] and data['status'] == 'unavailable'
    assert data['summary'] is None and data['calls'] == []
    store.create_retries('retry', [identity], {identity: OPTIONS})
    store.claim()
    store.recover()
    assert store.database.latest_recognition_calls(identity)['status'] == 'interrupted'


def test_deleted_receipt_removes_trace_and_late_retry_cannot_recreate_it(store):
    identity = save(store.database, snapshot=trace())
    store.create_retries('retry', [identity], {identity: OPTIONS})
    claimed = store.claim()
    store.database.delete_results([identity])
    store.finish(claimed, error='timeout', recognition_calls=trace(status='failed'))
    with store.database.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM latest_recognition_calls').fetchone()[0] == 0
    with pytest.raises(KeyError):
        store.database.latest_recognition_calls(identity)


def test_sample_and_production_routes_are_isolated_and_sample_urls_are_projected(samples):
    ctx, client = samples
    service = SampleCenter(ctx)
    sample_trace = trace()
    sample_trace['calls'][0]['image_url'] = '/files/artifacts/token/seal.png'
    sample_id = save(service.database, snapshot=sample_trace)
    production_id = save(ctx.database, snapshot=trace(name='production.png'))
    assert production_id == sample_id
    data = client.get(f'/api/samples/results/{sample_id}/recognition-calls').json
    assert data['status'] == 'succeeded'
    assert data['calls'][0]['image_url'] == '/files/samples/artifacts/token/seal.png'
    assert data['calls'][0]['input_name'] == 'seal-1.png'
    assert client.get('/api/samples/results/9999/recognition-calls').status_code == 404
    assert client.get(f'/api/samples/results/{sample_id}/runs').status_code == 404
    service.library.remove(['one.png'])
    with service.database.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM latest_recognition_calls').fetchone()[0] == 0
    assert ctx.database.latest_recognition_calls(production_id)['available']


def test_formal_route_rejects_deleted_receipt(web_queue):
    identity = save(web_queue.database, snapshot=trace())
    client = web_queue.app.test_client()
    assert client.get(f'/api/results/{identity}/recognition-calls').json['summary']['total'] == 1
    with web_queue.database.connect() as con:
        con.execute("UPDATE results SET deleted_at='2026-10-26' WHERE id=?", (identity,))
        assert con.execute('SELECT COUNT(*) FROM latest_recognition_calls').fetchone()[0] == 0
    assert client.get(f'/api/results/{identity}/recognition-calls').status_code == 404


def test_document_trace_urls_follow_existing_artifact_deduplication(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (20, 30), 'white').save(source)
    paths = {}
    def analyze(source, preview, **options):
        directory = options['artifact_dir']
        directory.mkdir(parents=True)
        paths['directory'] = directory
        for name in ('color.png', 'ring.png'):
            Image.new('RGB', (12, 8), 'red').save(directory / name)
        with recognition_call('paddle_v6', 'recognize', directory / 'ring.png'):
            pass
        return {'processing_artifacts': {'color_isolated_url': options['artifact_url_prefix'] + '/color.png'}}
    service = DocumentRecognitionService(analyze, tmp_path / 'previews', tmp_path / 'artifacts', lambda: 'now')
    payload, _ = service.recognize(source, filename='receipt.png')
    detail = payload['_recognition_calls']
    assert detail['summary']['total'] == 1
    assert detail['calls'][0]['image_url'].endswith('/color.png')
    assert detail['calls'][0]['width'] == 12 and detail['calls'][0]['height'] == 8
    assert len(list(paths['directory'].iterdir())) == 1


def test_direct_recognition_failure_keeps_measured_calls(web_queue, monkeypatch):
    def analyze(source, preview, **options):
        with recognition_call('paddle_v6', 'recognize', source):
            raise RuntimeError('timeout')
    monkeypatch.setattr(web_queue, '_configured_analyze', analyze)
    client = web_queue.app.test_client()
    response = client.post('/api/analyze', data={'file': (io.BytesIO(b'image'), 'one.png'),
                                               'ocr_backend': 'paddle_v6'})
    assert response.status_code == 500
    data = client.get(f"/api/results/{response.json['id']}/recognition-calls").json
    assert data['available'] and data['status'] == 'failed'
    assert data['summary']['total'] == data['summary']['failed'] == 1
