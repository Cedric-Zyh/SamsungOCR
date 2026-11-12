"""Durable asynchronous lanes; all transports are injected, never real uploads."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from PIL import Image

from receipt_ocr.danzhengtong import Settings
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.jobs.service import ReceiptJobService
from receipt_ocr.jobs.danzhengtong_worker import DanzhengtongWorker
from receipt_ocr.application.deferred_document import empty_snapshot
from receipt_ocr.persistence.database import Database


PLAN = {stage: ['danzhengtong'] for stage in ('fields', 'handwriting', 'date', 'seal')}
PLAN['products'] = []
COMMIT = {name: {'value': value, 'ratio': 1, 'position': []} for name, value in {
    '客户名称': '测试客户', '签章要求': '测试客户专用章', '收货客户印章': '测试客户专用章',
    '仓库联系人': '甲', '仓库接收人': '甲', '合计数量': '1', '实收数量': '', '拒收数量': '0',
    '要求到货': '2025-10-27', '签收日期': '2025-10-27'}.items()}


class FakeClient:
    def __init__(self):
        self.settings = Settings(app_id='test-id')
        self.uploads = self.submits = self.queries = 0
        self.ready = False
        self.query_error = None
        self.submit_error = None

    def _validate_credentials(self):
        pass

    def upload_file(self, *args, **kwargs):
        self.uploads += 1
        return {'fileId': 'saved-file'}

    def submit(self, filename, file_id):
        assert file_id == 'saved-file'
        self.submits += 1
        if self.submit_error:
            raise self.submit_error
        return {'code': 200, 'status': True, 'data': {'reqUuid': 'original-request'}}

    def get_result(self, req_uuid, **kwargs):
        assert req_uuid == 'original-request'
        self.queries += 1
        if self.query_error:
            raise self.query_error
        return {'code': 200, 'status': True, 'data': {
            'recordId': 1, 'commitResult': deepcopy(COMMIT) if self.ready else None}}


@pytest.fixture
def runtime(tmp_path):
    database = Database(tmp_path / 'results.db')
    database.initialize()
    store = JobStore(database)
    store.initialize()
    Image.new('RGB', (100, 100), 'white').save(tmp_path / 'saved.jpg')
    local_calls = []

    def analyze(source, preview, **options):
        local_calls.append(options)
        config = options['recognition_config']
        snapshot = empty_snapshot(config)
        fields = {'客户名称': '本地客户', '要求到货': '2025-10-26', '签章要求': '测试客户专用章'}
        snapshot['results']['fields'] = [{'method': 'paddle_v6', 'result': {'fields': fields,
            'field_metadata': {key: {'value': value, 'source': '本地'} for key, value in fields.items()}}}]
        return {'overall': '需人工复核', 'review_status': '待复核', 'fields': fields,
                '_assembly_snapshot': snapshot}

    service = ReceiptJobService(analyze, data_dir=tmp_path, upload_dir=tmp_path, preview_dir=tmp_path, artifact_dir=tmp_path)
    client = FakeClient()
    worker = DanzhengtongWorker(store, service, client_factory=lambda task: client)
    return SimpleNamespace(store=store, service=service, client=client, worker=worker,
                           directory=tmp_path, local_calls=local_calls)


def start(runtime, *, mixed=False, mode='all', task_id='task'):
    plan = deepcopy(PLAN)
    if mixed:
        plan['fields'].insert(0, 'paddle_v6')
    settings = runtime.store.database.get_json_setting('recognition_defaults')
    settings['recognition_config'] = plan
    runtime.store.database.set_json_setting('recognition_defaults', settings)
    task = runtime.store.create_batch(task_id, '测试', [{'filename': 'receipt.jpg'}], {
        'recognition_config': plan, 'ocr_backend': 'auto', 'seal_recognition_mode': 'local'})
    job_id = task['items'][0]['id']
    runtime.store.accept_upload(job_id, 'saved.jpg')
    runtime.store.start_jobs([job_id], mode)
    return job_id


def finish_regular(runtime):
    claimed = runtime.store.claim()
    result, preview = runtime.service(claimed)
    runtime.store.finish(claimed, result, preview)
    return runtime.store.get(claimed['id'])


@pytest.mark.parametrize('sources', [
    ['paddle_v6'], ['paddle_v6', 'qingtong'], ['paddle_v6', 'danzhengtong'],
    ['paddle_v6', 'qingtong', 'danzhengtong'],
])
def test_one_start_only_schedules_sources_enabled_in_current_settings(runtime, sources):
    config = {stage: ['paddle_v6'] for stage in ('fields', 'handwriting', 'date', 'seal')}
    config.update(products=[], seal=list(sources))
    settings = runtime.store.database.get_json_setting('recognition_defaults')
    settings['recognition_config'] = config
    runtime.store.database.set_json_setting('recognition_defaults', settings)
    task = runtime.store.create_batch('selected-sources', '测试', [{'filename': 'receipt.jpg'}], {
        'recognition_config': config, 'ocr_backend': 'auto', 'seal_recognition_mode': 'local'})
    job_id = task['items'][0]['id']
    runtime.store.accept_upload(job_id, 'saved.jpg')
    runtime.store.start_jobs([job_id])
    saved = json.loads(runtime.store.get(job_id)['payload_json'])['recognition_config']
    assert saved['seal'] == sources
    with runtime.store.database.connect() as con:
        count = con.execute('SELECT COUNT(*) FROM danzhengtong_jobs').fetchone()[0]
    assert count == int('danzhengtong' in sources)
    claimed = runtime.store.claim()
    assert json.loads(claimed['payload_json'])['recognition_config']['seal'] == sources


def query(runtime, job_id):
    runtime.store.remote_action([job_id], 'query')
    assert runtime.worker.step()


def test_pending_survives_restart_and_only_queries_original_request(runtime):
    job_id = start(runtime)
    assert runtime.worker.step()  # POST independently of the regular branch
    assert runtime.store.remote_detail(job_id)['req_uuid'] == 'original-request'
    job = finish_regular(runtime)
    result_id = job['result_id']
    assert runtime.store.database.get_result(result_id)['overall'] == '待结果'
    query(runtime, job_id)
    assert runtime.store.remote_detail(job_id)['status'] == 'waiting'
    reopened = JobStore(Database(runtime.store.database.path))
    reopened.database.initialize()
    reopened.initialize()
    assert reopened.database.enforce_uncertain_review_queue() == 0
    reopened.recover()
    reopened.recover_remote()
    runtime.store = reopened
    runtime.worker = DanzhengtongWorker(reopened, runtime.service, client_factory=lambda task: runtime.client)
    runtime.client.ready = True
    query(runtime, job_id)
    assert runtime.store.remote_detail(job_id)['status'] == 'received'
    assert runtime.worker.step()  # local application of durable evidence
    actual = reopened.database.get_result(result_id)
    assert actual['fields']['客户名称'] == '测试客户'
    assert actual['fields']['拒收数量'] == '0'
    assert actual['date_check']['actual'] == '2025-10-27'
    assert actual['danzhengtong']['reqUuid'] == 'original-request'
    assert reopened.remote_detail(job_id)['status'] == 'applied'
    assert (runtime.client.uploads, runtime.client.submits, runtime.client.queries) == (1, 1, 2)
    assert reopened.database.list_results()[0]['id'] == result_id


def test_local_branch_finishes_while_remote_waits_and_is_not_run_again(runtime):
    job_id = start(runtime, mixed=True)
    runtime.worker.step()
    job = finish_regular(runtime)
    assert len(runtime.local_calls) == 1
    assert all('danzhengtong' not in methods for methods in runtime.local_calls[0]['recognition_config'].values() if isinstance(methods, list))
    assert runtime.store.database.get_result(job['result_id'])['fields']['客户名称'] == '本地客户'
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert len(runtime.local_calls) == 1
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'


def test_network_error_keeps_request_queryable(runtime):
    job_id = start(runtime)
    runtime.worker.step()
    runtime.client.query_error = TimeoutError('temporary timeout')
    query(runtime, job_id)
    actual = runtime.store.remote_detail(job_id)
    assert actual['status'] == 'waiting' and actual['req_uuid'] == 'original-request'
    assert 'temporary timeout' in actual['error_message']
    assert runtime.client.submits == runtime.client.uploads == 1


def test_human_edits_are_not_overwritten_by_late_result(runtime):
    job_id = start(runtime)
    runtime.worker.step()
    job = finish_regular(runtime)
    current = runtime.store.database.get_result(job['result_id'])
    current['fields']['客户名称'] = '人工修改'
    runtime.store.database.review_result(current['id'], result=current, review_status='待复核',
        final_result='待结果', note='保留修改', action='人工修改')
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'conflict'
    assert runtime.store.remote_detail(job_id)['result']['commitResult']['客户名称']['value'] == '测试客户'
    assert runtime.store.database.get_result(current['id'])['fields']['客户名称'] == '人工修改'


def test_unknown_submission_is_not_automatically_repeated(runtime):
    job_id = start(runtime)
    runtime.client.submit_error = TimeoutError('reply lost')
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'unknown'
    runtime.store.recover_remote()
    assert not runtime.worker.step()
    runtime.store.remote_action([job_id], 'submit')
    assert not runtime.worker.step()
    assert runtime.client.uploads == runtime.client.submits == 1


def test_controls_and_concurrent_claims_are_independent(runtime):
    job_id = start(runtime)
    runtime.store.set_paused(True)
    assert runtime.store.claim() is None
    assert runtime.worker.step()  # regular pause does not pause the remote branch
    runtime.store.remote_control({'auto_query': False, 'submit_paused': True})
    assert not runtime.worker.step()
    runtime.store.remote_action([job_id], 'query')
    first = runtime.store.claim_remote()
    assert first['req_uuid'] == 'original-request'
    assert runtime.store.claim_remote() is None


def test_deleted_receipt_cannot_be_resurrected(runtime):
    job_id = start(runtime)
    runtime.worker.step()
    job = finish_regular(runtime)
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.store.database.delete_results([job['result_id']])
    assert not runtime.worker.step()
    assert runtime.store.database.list_results() == []


def test_local_only_does_not_submit_until_explicitly_requested(runtime):
    job_id = start(runtime, mixed=True, mode='local')
    finish_regular(runtime)
    assert not runtime.worker.step()
    runtime.store.remote_action([job_id], 'submit')
    assert runtime.worker.step()
    assert runtime.client.uploads == 1


def test_only_remote_can_be_followed_by_local_without_another_upload(runtime):
    job_id = start(runtime, mixed=True, mode='danzhengtong')
    runtime.worker.step()
    finish_regular(runtime)
    assert runtime.local_calls == []
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'
    runtime.store.start_jobs([job_id], 'local')
    finish_regular(runtime)
    runtime.worker.step()
    assert len(runtime.local_calls) == 1
    assert runtime.client.uploads == runtime.client.submits == 1


def test_crash_before_regular_result_still_resumes_the_accepted_request(runtime):
    job_id = start(runtime, mixed=True)
    runtime.store.claim()  # interrupted before the regular result is saved
    runtime.worker.step()  # remote acceptance has already been committed
    runtime.store.recover()
    runtime.store.recover_remote()
    finish_regular(runtime)
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'
    assert runtime.client.submits == runtime.client.uploads == 1


def test_later_submission_after_regular_only_plan_can_apply_without_rerunning_ocr(runtime):
    job_id = start(runtime, mixed=True, mode='local')
    finish_regular(runtime)
    runtime.store.remote_action([job_id], 'submit')
    runtime.worker.step()
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'
    assert len(runtime.local_calls) == 1


def test_cannot_apply_response_to_a_different_image_version(runtime):
    job_id = start(runtime)
    finish_regular(runtime)
    Image.new('RGB', (200, 200), 'blue').save(runtime.directory / 'saved.jpg')
    runtime.worker.step()
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'conflict'
    assert '图片版本' in runtime.store.remote_detail(job_id)['error_message']


def test_remote_status_survives_management_list_hydration_and_api_queries(bind_application_runtime, runtime, monkeypatch):
    from receipt_ocr.web import application as web
    bind_application_runtime(database=runtime.store.database, job_store=runtime.store)
    job_id = start(runtime)
    runtime.worker.step()
    job = finish_regular(runtime)
    client = web.app.test_client()
    day = job['created_at'][:10]
    queue = client.get('/api/queue', query_string={'import_date': day, 'include_related': 1}).get_json()
    assert queue[0]['danzhengtong']['req_uuid'] == 'original-request'
    records = client.get('/api/results', query_string={'import_date': day, 'include_processing': 1, 'page': 1}).get_json()
    assert records['items'][0]['danzhengtong_task']['status'] == 'waiting'
    assert records['status_counts']['处理中'] == 0
    assert records['status_counts']['需人工复核'] == 1
    assert records['items'][0]['final_result'] == '待结果'
    assert client.post('/api/danzhengtong/jobs', json={'ids': [job_id], 'action': 'query'}).status_code == 202
    assert client.get(f'/api/jobs/{job_id}/danzhengtong').get_json()['req_uuid'] == 'original-request'
    assert client.patch('/api/danzhengtong/control', json={'auto_query': False}).get_json()['auto_query'] is False
    assert runtime.client.submits == 1  # UI queries only enqueue GET work


def test_regular_failure_finishes_progress_but_waits_for_independent_remote_verdict(bind_application_runtime, runtime, monkeypatch):
    from receipt_ocr.web import application as web
    bind_application_runtime(database=runtime.store.database, job_store=runtime.store)
    job_id = start(runtime)
    runtime.store.finish(runtime.store.claim(), error='常规识别暂时失败')
    task = runtime.store.task('task')
    assert task['completed'] == 1 and task['failed'] == 1
    assert task['status'] == '已完成' and task['remote_pending'] == 1
    client = web.app.test_client()
    day = runtime.store.get(job_id)['created_at'][:10]
    data = client.get('/api/results', query_string={'import_date': day, 'include_processing': 1}).get_json()
    assert data['status_counts']['处理中'] == 0
    assert data['status_counts']['需人工复核'] == 1
    assert data['items'][0]['final_result'] == '待结果'
    assert not data['items'][0].get('processing_status')
    assert runtime.worker.step()  # Remote submission can still run.
    runtime.client.ready = True
    query(runtime, job_id)
    assert runtime.worker.step()
    assert runtime.store.get(job_id)['status'] == 'succeeded'
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'


def test_received_evidence_is_retried_locally_after_apply_failure(runtime, monkeypatch):
    job_id = start(runtime)
    runtime.worker.step()
    finish_regular(runtime)
    runtime.client.ready = True
    query(runtime, job_id)
    original = runtime.store.apply_remote
    monkeypatch.setattr(runtime.store, 'apply_remote', lambda *args: (_ for _ in ()).throw(RuntimeError('db busy')))
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'received'
    monkeypatch.setattr(runtime.store, 'apply_remote', original)
    runtime.store.remote_action([job_id], 'query')
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'
    assert runtime.client.queries == 1


def test_new_regular_retry_reuses_the_original_remote_submission(runtime):
    job_id = start(runtime, mixed=True)
    runtime.worker.step()
    first = finish_regular(runtime)
    options = json.loads(first['payload_json'])
    task = runtime.store.create_retries('retry', [first['result_id']], {first['result_id']: options})
    new_id = task['items'][0]['id']
    finish_regular(runtime)
    assert runtime.store.remote_detail(new_id)['req_uuid'] == 'original-request'
    runtime.client.ready = True
    query(runtime, new_id)
    runtime.worker.step()
    assert runtime.store.remote_detail(new_id)['status'] == 'applied'
    assert runtime.client.submits == runtime.client.uploads == 1


def test_explicit_submission_rejection_is_distinct_from_unknown_reply(runtime):
    from receipt_ocr.danzhengtong_errors import provider_error
    job_id = start(runtime)
    runtime.client.submit_error = provider_error('提交识别', runtime.client.settings,
        body={'code': 'RATE_LIMIT', 'status': False, 'message': '请稍后提交'})
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['status'] == 'failed'


def test_remote_only_finishes_even_while_regular_queue_is_paused(runtime):
    job_id = start(runtime, mixed=True, mode='danzhengtong')
    runtime.store.set_paused(True)
    runtime.worker.step()
    runtime.client.ready = True
    query(runtime, job_id)
    runtime.worker.step()  # save the remote-only document without a local claim
    runtime.worker.step()  # finalize durable application status
    assert runtime.store.remote_detail(job_id)['status'] == 'applied'
    assert runtime.store.get(job_id)['status'] == 'succeeded'
    assert runtime.local_calls == []
    assert runtime.store.is_paused()


def test_busy_database_after_acceptance_retries_persistence_not_submission(runtime, monkeypatch):
    import sqlite3
    job_id = start(runtime)
    original = runtime.store.update_remote
    attempts = []
    def persist(task, **changes):
        if changes.get('req_uuid') and not attempts:
            attempts.append(True)
            raise sqlite3.OperationalError('database is busy')
        return original(task, **changes)
    monkeypatch.setattr(runtime.store, 'update_remote', persist)
    runtime.worker.step()
    assert runtime.store.remote_detail(job_id)['req_uuid'] == 'original-request'
    assert runtime.client.submits == runtime.client.uploads == 1
