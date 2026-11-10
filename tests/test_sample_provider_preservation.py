"""A remote-only sample refresh retains the other providers and their evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from sample_support import SampleCenter
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from test_sample_center import samples
from test_danzhengtong_jobs import FakeClient, COMMIT


def legacy_result():
    fields = {'客户名称': '原客户', '签章要求': '原签章要求', '要求到货': '2025-10-26',
              '仓库联系人': '甲', '仓库接收人': '甲'}
    local_date = {'status': '不匹配', 'actual': '2025-10-25', 'required': '2025-10-26', 'reliable': True}
    local_seal = {'status': '不匹配', 'recognized': '原本地章', 'requirement': '原签章要求', 'reliable': True}
    qingtong = {'status': '匹配', 'recognized': '原清瞳章', 'requirement': '原签章要求', 'reliable': True,
        'api': {'ok': True, 'raw': '清瞳原始报文'}, 'dual_check': {'selected': {
            'template': {'label': '原清瞳模板', 'requirement_match': {'status': '匹配'}},
            'ocr': {'text': '原清瞳文字', 'comparison': {'status': '不匹配'}}}}}
    plan = deepcopy(DEFAULT_RECOGNITION_SETTINGS['recognition_config'])
    plan['products'] = ['paddle_v6']
    plan['seal'] = ['paddle_v6', 'qingtong', 'danzhengtong']
    products = {'rows': [{'values': {'商品名称': '原商品', '数量': '8'}}], 'status': '已执行'}
    variants = {
        'fields': [{'method': 'paddle_v6', 'value': fields, 'error': None,
                    'details': {'fields': fields, 'field_metadata': {}}}],
        'products': [{'method': 'paddle_v6', 'value': products['rows'], 'error': None,
                      'details': {'product_table': products}}],
        'handwriting': [{'method': 'paddle_v6', 'value': {'仓库接收人': '甲'}, 'error': None,
                         'details': {'handwriting_fields': {'仓库接收人': '甲'}, 'handwriting_metadata': {}}}],
        'date': [{'method': 'paddle_v6', 'value': ['2025-10-25', '不匹配'], 'error': None,
                  'details': {'date_check': local_date}}],
        'seal': [{'method': 'paddle_v6', 'value': ['原本地章', '不匹配'], 'error': None,
                  'details': {'seal_check': local_seal}},
                 {'method': 'qingtong', 'value': ['原清瞳章', '匹配'], 'error': None,
                  'details': {'seal_check': qingtong}},
                 {'method': 'danzhengtong', 'value': ['旧单证通章', '不匹配'], 'error': None,
                  'details': {'seal_check': {'status': '不匹配', 'recognized': '旧单证通章'}}}],
    }
    return {'overall': '需人工复核', 'review_status': '待复核', 'review_issues': [], 'fields': fields,
        'date_check': local_date, 'seal_check': qingtong, 'product_table': products,
        'signature_check': {'status': '匹配'}, 'recognition_config': plan,
        'recognition_variants': variants, 'processing_seconds': 12,
        'processing_timings': {'local': 7, 'qingtong': 5}, 'preview_url': '/files/samples/previews/old.jpg',
        'processing_artifacts': {'date': [{'original_url': '/files/samples/artifacts/date.png'}],
                                'seals': [{'crop_url': '/files/samples/artifacts/seal.png'}],
                                'signature_requirement': [{'image': '原签章要求图片'}]},
        'raw_text': '本地原文证据', 'qr_text': '原二维码', 'ocr_observations': []}


def seed_legacy(ctx):
    service = SampleCenter(ctx)
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    options = {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
               'recognition_config': settings['recognition_config']}
    service.store.enqueue(['one.png'], options, settings)
    job = service.store.claim()
    service.store.finish(job, legacy_result(), 'old.jpg')
    job = service.store.get(job['id'])
    return service, job, service.database.get_result(job['result_id'])


def other_variants(result):
    return {stage: [variant for variant in variants if variant['method'] != 'danzhengtong']
            for stage, variants in result['recognition_variants'].items()}


def test_remote_refresh_keeps_other_results_while_waiting_and_after_return(samples):
    ctx, client = samples
    service, original_job, original = seed_legacy(ctx)
    ctx._configured_analyze = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not rerun other providers'))
    response = client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    assert response.status_code == 202
    service = SampleCenter(ctx)
    item = next(row for row in service.listing()['items'] if row['filename'] == 'one.png')
    assert item['retained_results'] and item['status'] == '测试中'
    assert item['result_id'] != original_job['result_id']
    waiting = service.database.get_result(item['result_id'])
    assert other_variants(waiting) == other_variants(original)
    assert waiting['fields'] == original['fields']
    assert waiting['product_table'] == original['product_table']
    assert item['result_filters']['seals']['qingtong_ocr']['value'] == '原清瞳文字'
    assert item['result_filters']['seals']['local:paddle_v6']['value'] == '原本地章'
    assert service.store.claim() is None
    provider = FakeClient()
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()  # One upload and submit.
    provider.ready = True
    service.store.remote_action([item['job_id']], 'query')
    assert service.worker.remote.step()
    assert service.worker.remote.step()  # Apply the saved provider result.
    result = service.database.get_result(item['result_id'])
    assert other_variants(result) == other_variants(original)
    assert result['product_table'] == original['product_table']
    assert result['processing_artifacts'] == original['processing_artifacts']
    assert result['preview_url'] == original['preview_url']
    assert result['processing_timings'] == original['processing_timings']
    assert result['raw_text'] == original['raw_text']
    assert result['qr_text'] == original['qr_text']
    seal_variants = result['recognition_variants']['seal']
    assert [v['method'] for v in seal_variants] == ['paddle_v6', 'qingtong', 'danzhengtong']
    # This blank fixture has no stamp crop. The old page-scene stamp text
    # must never stand in for a result from the new independent stamp scene.
    assert seal_variants[-1]['details']['seal_check']['recognized'] == ''
    assert service.database.get_result(original_job['result_id']) == original
    assert provider.uploads == provider.submits == 1
    # Filters and statistics use the retained providers, not just the requested scope.
    latest = next(row for row in service.listing()['items'] if row['filename'] == 'one.png')
    assert latest['result_filters']['seals']['qingtong_ocr']['value'] == '原清瞳文字'
    stats = client.get('/api/samples/statistics').json
    row = next(row for row in stats['items'] if row['filename'] == 'one.png')
    assert row['seals']['qingtong_ocr']['value'] == '原清瞳文字'


def test_failed_query_does_not_clear_retained_evidence(samples):
    ctx, client = samples
    _, _, original = seed_legacy(ctx)
    client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    service = SampleCenter(ctx)
    provider = FakeClient()
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()
    item = next(row for row in service.listing()['items'] if row['filename'] == 'one.png')
    provider.query_error = TimeoutError('temporary timeout')
    service.store.remote_action([item['job_id']], 'query')
    assert service.worker.remote.step()
    result = service.database.get_result(item['result_id'])
    assert result['fields'] == original['fields']
    assert other_variants(result) == other_variants(original)
    assert service.store.remote_detail(item['job_id'])['status'] == 'waiting'


@pytest.mark.parametrize('received', [True, False])
def test_existing_remote_only_run_recovers_history_without_resubmission(samples, received):
    ctx, _ = samples
    service, original_job, original = seed_legacy(ctx)
    # Reproduce the pre-fix format: a separate remote-only result with no retained evidence.
    plan = {stage: ['danzhengtong'] for stage in ('fields', 'handwriting', 'date', 'seal')}
    plan['products'] = []
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    service.store.enqueue(['one.png'], {'ocr_backend': 'auto', 'seal_recognition_mode': 'local',
                                      'recognition_config': plan}, settings)
    job = service.store.claim()
    service.store.finish(job, {'fields': {'客户名称': '单证通客户'}, 'recognition_config': plan, 'overall': '通过'})
    with service.database.connect() as con:
        payload = json.loads(job['payload_json'])
        payload.update(_sample_execution_mode='danzhengtong', _execution_scope='danzhengtong')
        payload['sample_options'].update(_sample_execution_mode='danzhengtong', _execution_scope='danzhengtong')
        con.execute('UPDATE recognition_jobs SET payload_json=? WHERE id=?', (json.dumps(payload), job['id']))
        digest = hashlib.sha256(service.library.source('one.png').read_bytes()).hexdigest()
        con.execute("UPDATE danzhengtong_jobs SET status=?,req_uuid='saved-request',result_json=?,source_hash=?,seal_requests_json='{}' WHERE job_id=?",
                    ('applied' if received else 'waiting',
                     json.dumps({'commitResult': deepcopy(COMMIT)}) if received else '{}', digest, job['id']))
    restored = SampleCenter(ctx)
    task = restored.store.remote_detail(job['id'])
    assert task['status'] == ('received' if received else 'waiting') and task['req_uuid'] == 'saved-request'
    provider = FakeClient()
    if received:
        restored.worker.remote.client_factory = lambda task: (_ for _ in ()).throw(AssertionError('must not make a network request'))
    else:
        def query(req_uuid, **kwargs):
            assert req_uuid == 'saved-request'
            provider.queries += 1
            return {'data': {'commitResult': deepcopy(COMMIT)}}

        provider.get_result = query
        restored.worker.remote.client_factory = lambda task: provider
        restored.store.remote_action([job['id']], 'query')
        assert restored.worker.remote.step()
    assert restored.worker.remote.step()
    current = restored.database.get_result(restored.store.get(job['id'])['result_id'])
    assert other_variants(current) == other_variants(original)
    assert current['danzhengtong']['reqUuid'] == 'saved-request'
    assert restored.store.remote_detail(job['id'])['status'] == 'applied'
    assert provider.uploads == provider.submits == 0
    assert provider.queries == (0 if received else 1)
    assert restored.database.get_result(original_job['result_id']) == original
    # Restoration is idempotent and never schedules another upload or query.
    reopened = SampleCenter(ctx)
    assert reopened.store.remote_detail(job['id'])['status'] == 'applied'
    assert not reopened.worker.remote.step()
