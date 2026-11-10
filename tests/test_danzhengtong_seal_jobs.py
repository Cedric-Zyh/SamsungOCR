"""Independent stamp requests survive interruption and never reuse page stamp text."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from receipt_ocr.jobs.danzhengtong_worker import DanzhengtongWorker
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.persistence.database import Database
from test_danzhengtong_jobs import runtime, start, finish_regular, query


@pytest.fixture
def stamps(runtime, monkeypatch):
    prepared_calls, uploads, submissions, queries = [], [], [], []
    submit_error, query_error = [], []

    def prepare(source, artifact_dir, prefix=''):
        prepared_calls.append(str(source))
        directory = Path(artifact_dir) / 'seals'
        directory.mkdir(parents=True, exist_ok=True)
        inputs = []
        for index, color in enumerate(('blue', 'red')):
            image = directory / f'stamp-{index}-input.png'
            Image.new('RGB', (40 + index, 23), color).save(image)
            inputs.append({'index': index, 'input_path': str(image),
                           'input_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                           'input_url': f'{prefix}/seals/{image.name}',
                           'original_url': f'{prefix}/seals/{image.name}', 'color': color,
                           'region': {'x': 0.1, 'y': 0.7, 'width': 0.2, 'height': 0.1,
                                      'role': 'customer', 'color': color}})
        return inputs

    class SealClient:
        settings = replace(runtime.client.settings, model_id=runtime.client.settings.seal_model_id)

        def _validate_credentials(self):
            pass

        def upload_file(self, path):
            uploads.append((str(path), Path(path).read_bytes()))
            return {'fileId': Path(path).name}

        def submit(self, filename, file_id):
            assert filename == file_id
            submissions.append(filename)
            if submit_error:
                raise submit_error[0]
            return {'code': 200, 'status': True, 'data': {'reqUuid': 'seal-' + filename}}

        def get_result(self, req_uuid, **kwargs):
            # The UUID is durable before the first GET, including after reopen.
            with runtime.store.database.connect() as con:
                states = [json.loads(row[0]) for row in con.execute('SELECT seal_requests_json FROM danzhengtong_jobs')]
            assert any(item.get('req_uuid') == req_uuid for state in states for item in state.get('requests', []))
            queries.append(req_uuid)
            if query_error:
                raise query_error[0]
            second = 'stamp-1-' in req_uuid
            return {'code': 200, 'status': True, 'data': {'commitResult': {
                name: {'value': value, 'ratio': 1, 'position': []} for name, value in {
                    '收货客户印章': '测试客户专用章' if second else '另一家客户收货章',
                    '印章颜色': '红色' if second else '蓝色',
                    '印章类型': '圆章' if second else '椭圆章'}.items()}}}

    seal_client = SealClient()
    factory = lambda task: seal_client if task.get('_seal_request') else runtime.client
    runtime.worker.client_factory = factory
    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.prepare_requests', prepare)
    runtime.stamp_state = dict(prepared=prepared_calls, uploads=uploads, submissions=submissions,
                               queries=queries, submit_error=submit_error, query_error=query_error, factory=factory)
    return runtime


def start_seal_only(service):
    plan = {stage: [] for stage in ('fields', 'handwriting', 'date', 'seal', 'products')}
    plan['seal'] = ['danzhengtong']
    settings = service.store.database.get_json_setting('recognition_defaults')
    settings['recognition_config'] = plan
    service.store.database.set_json_setting('recognition_defaults', settings)
    task = service.store.create_batch('stamps', '测试', [{'filename': 'receipt.jpg'}], {
        'recognition_config': plan, 'previous_fields': {'签章要求': '测试客户专用章'},
        'ocr_backend': 'auto', 'seal_recognition_mode': 'local'})
    job_id = task['items'][0]['id']
    service.store.accept_upload(job_id, 'saved.jpg')
    service.store.start_jobs([job_id], 'danzhengtong')
    return job_id


def complete(service, job_id):
    for _ in range(20):
        status = service.store.remote_detail(job_id)['status']
        if status in {'applied', 'failed', 'unknown', 'conflict'}:
            return
        if status == 'waiting':
            service.store.remote_action([job_id], 'query')
        assert service.worker.step()
    raise AssertionError('stamp task did not finish')


@pytest.mark.parametrize('fail', [False, True])
def test_async_stamp_preparation_shares_one_decode_and_releases_cache(runtime, monkeypatch, fail):
    from receipt_ocr import danzhengtong_seal
    from receipt_ocr.imaging import detection, io, working_images
    from receipt_ocr.imaging.seal_detector import SealDetection
    from receipt_ocr.jobs.danzhengtong_seals import seal_state

    reads, workspaces = [], []
    decode = io._decode_pixels

    def read(path):
        reads.append(Path(path))
        workspaces.append(working_images.current_workspace())
        return decode(path)

    monkeypatch.setattr(io, '_decode_pixels', read)
    monkeypatch.setattr(detection, 'detect_seal_boxes', lambda image: [
        SealDetection((55, 65, 75, 85), .9), SealDetection((78, 65, 98, 85), .9),
    ])
    if fail:
        def failed_crop(*args):
            raise ValueError('test crop failed')
        monkeypatch.setattr(danzhengtong_seal, 'save_color_isolated_seal', failed_crop)

    start_seal_only(runtime)
    task = runtime.store.claim_remote()
    state = seal_state(task)
    assert runtime.worker._prepare_seals(task, state, finish=False)
    assert reads == [runtime.directory / 'saved.jpg']
    assert len(workspaces) == 1 and workspaces[0] is not None
    assert not workspaces[0].frames and workspaces[0].bytes == 0
    assert working_images.current_workspace() is None
    assert state['prepared']
    if fail:
        assert not state['requests'] and 'test crop failed' in state['preparation_error']
    else:
        assert len(state['requests']) == 2
        assert all(Path(item['input_path']).is_file() for item in state['requests'])
    assert runtime.client.uploads == runtime.client.submits == runtime.client.queries == 0


def test_page_and_stamp_scenes_use_separate_requests_and_each_stamp_keeps_its_text(stamps):
    job_id = start(stamps, mixed=True)
    assert stamps.worker.step()  # page scene is still the first physical POST
    regular = finish_regular(stamps)
    stamps.client.ready = True
    complete(stamps, job_id)
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['danzhengtong']['reqUuid'] == 'original-request'
    assert result['seal_check']['recognized'] == '测试客户专用章'
    assert result['seal_check']['provider_color'] == '红色'
    assert result['seal_check']['provider_seal_type'] == '圆章'
    assert result['danzhengtong_seal']['model_id'] == 'logistics_samsung_outseal_1503'
    assert len(result['seal_check']['candidates']) == 2
    assert stamps.client.uploads == stamps.client.submits == stamps.client.queries == 1
    assert len(stamps.stamp_state['submissions']) == len(stamps.stamp_state['queries']) == 2
    detail = stamps.store.database.latest_recognition_calls(regular['result_id'])
    remote = [row for row in detail['calls'] if row['method'] == 'danzhengtong']
    assert len(remote) == 3
    assert sorted(row['request_scope'] for row in remote) == ['page', 'seal', 'seal']
    assert all(row['transport']['query_count'] == 1 for row in remote)
    assert all(row['transport']['applied_at'] for row in remote)


def test_stamp_only_skips_whole_page_and_restart_only_queries_saved_uuids(stamps):
    job_id = start_seal_only(stamps)
    assert stamps.worker.step() and stamps.worker.step()  # exactly two stamp POSTs
    assert stamps.client.uploads == stamps.client.submits == 0
    reopened = JobStore(Database(stamps.store.database.path))
    reopened.database.initialize()
    reopened.initialize()
    reopened.recover_remote()
    stamps.store = reopened
    stamps.worker = DanzhengtongWorker(reopened, stamps.service,
                                       client_factory=stamps.stamp_state['factory'])
    complete(stamps, job_id)
    job = reopened.get(job_id)
    result = reopened.database.get_result(job['result_id'])
    assert result['seal_check']['status'] == '匹配'
    assert not result.get('danzhengtong')
    assert len(stamps.stamp_state['prepared']) == 1
    assert len(stamps.stamp_state['uploads']) == len(stamps.stamp_state['submissions']) == 2
    assert len(stamps.stamp_state['queries']) == 2
    assert reopened.remote_detail(job_id)['req_uuid'].startswith('seal-')


def test_lost_stamp_post_reply_never_automatically_resubmits(stamps):
    job_id = start_seal_only(stamps)
    stamps.stamp_state['submit_error'].append(TimeoutError('reply lost'))
    assert stamps.worker.step()
    # A second queued stamp may still be attempted, but the uncertain first
    # stamp itself must never be sent twice.
    stamps.worker.step()
    assert stamps.store.remote_detail(job_id)['status'] == 'unknown'
    stamps.store.recover_remote()
    assert not stamps.worker.step()
    assert len(set(stamps.stamp_state['submissions'])) == len(stamps.stamp_state['submissions']) == 2


def test_no_stamp_detection_cannot_fall_back_to_old_page_scene_text(runtime, monkeypatch):
    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.prepare_requests', lambda *args: [])
    job_id = start(runtime, mixed=True)
    runtime.worker.step()
    regular = finish_regular(runtime)
    runtime.client.ready = True
    complete(runtime, job_id)
    result = runtime.store.database.get_result(regular['result_id'])
    assert result['danzhengtong']['result']['commitResult']['收货客户印章']['value'] == '测试客户专用章'
    assert result['seal_check']['recognized'] == ''
    assert result['seal_check']['status'] != '匹配'


def test_page_post_with_lost_reply_does_not_block_independent_stamps_or_repeat_page(stamps):
    job_id = start(stamps, mixed=True)
    stamps.client.submit_error = TimeoutError('lost page reply')
    assert stamps.worker.step()
    assert stamps.store.remote_detail(job_id)['seal_result']['page']['status'] == 'unknown'
    assert stamps.store.remote_detail(job_id)['status'] == 'queued'
    stamps.store.recover_remote()
    regular = finish_regular(stamps)
    complete(stamps, job_id)
    assert stamps.client.uploads == stamps.client.submits == 1
    assert len(stamps.stamp_state['submissions']) == len(stamps.stamp_state['queries']) == 2
    result = stamps.store.database.get_result(regular['result_id'])
    assert result['seal_check']['recognized'] == '测试客户专用章'
    assert result['danzhengtong']['status'] == 'unknown'
    assert any(variant.get('error') for variant in result['recognition_variants']['date'])
    assert not any(variant.get('pending') for variant in result['recognition_variants']['date'])


def test_explicit_stamp_post_rejection_counts_attempts_and_retains_each_failure(stamps):
    job_id = start_seal_only(stamps)
    error = RuntimeError('scene rejected')
    error.provider_rejected = True
    stamps.stamp_state['submit_error'].append(error)
    complete(stamps, job_id)
    job = stamps.store.get(job_id)
    result = stamps.store.database.get_result(job['result_id'])
    assert result['seal_check']['status'] == '识别失败'
    detail = stamps.store.database.latest_recognition_calls(job['result_id'])
    assert detail['summary']['total'] == detail['summary']['failed'] == 2
    assert all(call['recognition_invoked'] for call in detail['calls'])
    assert not stamps.stamp_state['queries']


def test_failed_stamp_get_is_measured_in_both_transport_and_call_seconds(stamps):
    job_id = start_seal_only(stamps)
    stamps.worker.step()
    stamps.worker.step()
    stamps.stamp_state['query_error'].append(TimeoutError('temporary query failure'))
    query(stamps, job_id)
    child = stamps.store.remote_detail(job_id)['seal_result']['requests'][0]
    assert child['status'] == 'waiting' and child['query_count'] == 1
    assert 'temporary query failure' in child['error']
    call = child['call']
    assert call['seconds'] == pytest.approx(call['transport']['submit_seconds'] + call['transport']['query_seconds'], abs=0.0001)
    assert call['transport']['spans'][-1]['status'] == 'failed'
    stamps.stamp_state['query_error'].clear()
    complete(stamps, job_id)
    assert len(stamps.stamp_state['submissions']) == 2


def test_pausing_new_submissions_still_queries_accepted_page_and_stamp_requests(stamps):
    job_id = start(stamps, mixed=True)
    stamps.worker.step()  # page accepted; two stamp uploads still queued
    finish_regular(stamps)
    stamps.client.ready = True
    stamps.store.remote_control({'submit_paused': True})
    query(stamps, job_id)
    assert stamps.client.queries == 1 and not stamps.stamp_state['submissions']
    assert stamps.store.remote_detail(job_id)['status'] == 'queued'
    assert not stamps.worker.step()  # nothing else accepted yet
    stamps.store.remote_control({'submit_paused': False})
    assert stamps.worker.step()  # first stamp accepted
    stamps.store.remote_control({'submit_paused': True})
    query(stamps, job_id)
    assert len(stamps.stamp_state['queries']) == len(stamps.stamp_state['submissions']) == 1
    assert not stamps.worker.step()  # the second new stamp stays paused
    stamps.store.remote_control({'submit_paused': False})
    complete(stamps, job_id)
    assert len(stamps.stamp_state['submissions']) == 2


def test_disabling_auto_query_does_not_block_new_stamp_submissions(stamps):
    job_id = start_seal_only(stamps)
    stamps.store.remote_control({'auto_query': False})
    assert stamps.worker.step() and stamps.worker.step()
    assert len(stamps.stamp_state['submissions']) == 2 and not stamps.stamp_state['queries']
    assert not stamps.worker.step()
    query(stamps, job_id)
    assert len(stamps.stamp_state['queries']) == 1
    assert not stamps.worker.step()


def seed_prepared_requests(service, job_id, roles):
    """Reproduce crops queued by a version before shipper filtering existed."""
    from receipt_ocr.danzhengtong_seal import prepare_requests
    from receipt_ocr.jobs.danzhengtong_store import settings_identity
    with service.store.database.connect() as con:
        task = dict(con.execute('SELECT * FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone())
        source = service.service.source_for(service.store.get(job_id))
        directory = service.service.documents.artifact_dir / ('dzt-' + task['id'])
        prepared = prepare_requests(source, directory, '/files/artifacts/dzt-' + task['id'])
        for item, role in zip(prepared, roles):
            item.update(status='queued', req_uuid='', query_count=0)
            item['region']['role'] = role
        state = json.loads(task['seal_requests_json'])
        state.update(prepared=True, requests=prepared)
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=?,source_hash=?,environment_json=? WHERE id=?',
                    (json.dumps(state), hashlib.sha256(source.read_bytes()).hexdigest(),
                     json.dumps(settings_identity(service.client.settings)), task['id']))
    return prepared


def test_old_prepared_shipper_is_skipped_before_file_read_while_customer_uploads(stamps):
    job_id = start_seal_only(stamps)
    prepared = seed_prepared_requests(stamps, job_id, ['发货单位章', '客户章'])
    # A skipped crop need not remain available: it must not even reach hashing
    # or upload validation, while the genuine customer crop still runs.
    Path(prepared[0]['input_path']).unlink()
    complete(stamps, job_id)
    requests = stamps.store.remote_detail(job_id)['seal_result']['requests']
    assert requests[0]['status'] == 'skipped' and '发货单位章' in requests[0]['skip_reason']
    assert not requests[0].get('call') and not requests[0].get('error')
    assert requests[1]['status'] == 'completed'
    assert stamps.stamp_state['submissions'] == ['stamp-1-input.png']
    assert len(stamps.stamp_state['uploads']) == len(stamps.stamp_state['queries']) == 1
    result = stamps.store.database.get_result(stamps.store.get(job_id)['result_id'])
    assert result['seal_check']['status'] == '匹配'
    detail = stamps.store.database.latest_recognition_calls(result['id'])
    assert detail['summary']['total'] == detail['summary']['succeeded'] == 1
    assert detail['summary']['failed'] == 0


def test_old_customer_role_crop_identified_as_paired_shipper_is_skipped(stamps, monkeypatch):
    job_id = start_seal_only(stamps)
    prepared = seed_prepared_requests(stamps, job_id, ['收货客户章', '收货客户章'])
    examined = []

    def paired_senders(source, regions):
        examined.append((str(source), deepcopy(regions)))
        return {0}

    monkeypatch.setattr('receipt_ocr.danzhengtong_seal.sender_region_indices', paired_senders)
    Path(prepared[0]['input_path']).unlink()
    complete(stamps, job_id)
    requests = stamps.store.remote_detail(job_id)['seal_result']['requests']
    assert examined and len(examined[0][1]) == 2
    assert all(region['role'] == '收货客户章' for region in examined[0][1])
    assert requests[0]['status'] == 'skipped' and requests[1]['status'] == 'completed'
    assert stamps.stamp_state['submissions'] == ['stamp-1-input.png']
    assert len(stamps.stamp_state['uploads']) == len(stamps.stamp_state['queries']) == 1


@pytest.mark.parametrize('recovered', [False, True])
def test_only_prepared_shipper_crops_finish_without_failure_or_remote_calls(stamps, recovered):
    job_id = start_seal_only(stamps)
    seed_prepared_requests(stamps, job_id, ['发货单位章', '发货单位章'])
    if recovered:
        with stamps.store.database.connect() as con:
            task = con.execute('SELECT id,seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
            state = json.loads(task['seal_requests_json'])
            state['requests'][0]['status'] = 'uploading'
            con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                        (json.dumps(state), task['id']))
        stamps.store.recover_remote()
    complete(stamps, job_id)
    detail = stamps.store.remote_detail(job_id)
    assert detail['status'] == 'applied'
    assert all(item['status'] == 'skipped' and item.get('skip_reason') for item in detail['seal_result']['requests'])
    assert not stamps.stamp_state['uploads'] and not stamps.stamp_state['submissions'] and not stamps.stamp_state['queries']
    result = stamps.store.database.get_result(stamps.store.get(job_id)['result_id'])
    assert result['seal_check']['status'] != '识别失败'
    calls = stamps.store.database.latest_recognition_calls(result['id'])
    assert (calls.get('summary') or {}).get('total', 0) == 0
    assert (calls.get('summary') or {}).get('failed', 0) == 0


def test_shipper_request_already_accepted_keeps_its_uuid_and_is_only_queried(stamps):
    job_id = start_seal_only(stamps)
    assert stamps.worker.step()  # This older request was already accepted.
    with stamps.store.database.connect() as con:
        task = con.execute('SELECT id,seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(task['seal_requests_json'])
        accepted = state['requests'][0]['req_uuid']
        state['requests'][0]['region']['role'] = '发货单位章'
        state['requests'][0]['status'] = 'queued'  # Tolerate an old queued marker.
        state['requests'][1]['region']['role'] = '发货单位章'
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                    (json.dumps(state), task['id']))
    complete(stamps, job_id)
    requests = stamps.store.remote_detail(job_id)['seal_result']['requests']
    assert requests[0]['req_uuid'] == accepted and requests[0]['status'] == 'completed'
    assert requests[1]['status'] == 'skipped'
    assert len(stamps.stamp_state['uploads']) == len(stamps.stamp_state['submissions']) == 1
    assert stamps.stamp_state['queries'] == [accepted]


def test_shipper_with_uncertain_submit_marker_is_retained_without_resubmission(stamps):
    job_id = start_seal_only(stamps)
    seed_prepared_requests(stamps, job_id, ['发货单位章', '客户章'])
    with stamps.store.database.connect() as con:
        task = con.execute('SELECT id,seal_requests_json FROM danzhengtong_jobs WHERE job_id=?', (job_id,)).fetchone()
        state = json.loads(task['seal_requests_json'])
        state['requests'][0]['call'] = {'recognition_invoked': False, 'transport': {'phase': 'submitting'}}
        con.execute('UPDATE danzhengtong_jobs SET seal_requests_json=? WHERE id=?',
                    (json.dumps(state), task['id']))
    complete(stamps, job_id)
    requests = stamps.store.remote_detail(job_id)['seal_result']['requests']
    assert requests[0]['status'] == 'unknown' and '不重复提交' in requests[0]['error']
    assert not requests[0].get('skip_reason')
    assert requests[1]['status'] == 'completed'
    assert stamps.stamp_state['submissions'] == ['stamp-1-input.png']
    assert len(stamps.stamp_state['uploads']) == len(stamps.stamp_state['queries']) == 1
