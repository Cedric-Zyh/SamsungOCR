"""Sample execution choices must constrain real provider execution and recovery."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from receipt_ocr.application.plans import run_configured
from receipt_ocr.persistence.database import DEFAULT_RECOGNITION_SETTINGS
from sample_support import SampleCenter
from test_sample_center import samples
from test_sample_library import upload
from test_danzhengtong_jobs import FakeClient


@pytest.mark.parametrize('mode,providers', [
    ('local', {'paddle_v6'}), ('qingtong', {'qingtong'}),
    ('danzhengtong', {'danzhengtong'}), ('all', {'paddle_v6', 'qingtong', 'danzhengtong'}),
    ('both', {'paddle_v6', 'danzhengtong'}),
])
def test_explicit_modes_snapshot_only_selected_providers_and_remote_queue(samples, mode, providers):
    ctx, client = samples
    ctx.analyzer.seal_api.enabled = True
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    settings['recognition_config']['seal'] += ['qingtong', 'danzhengtong']
    ctx.database.set_json_setting('recognition_defaults', settings)
    response = client.post('/api/samples/run', json={'filenames': ['one.png', 'two.png'], 'mode': mode})
    assert response.status_code == 202
    service = SampleCenter(ctx)
    with service.database.connect() as con:
        queued = con.execute('SELECT payload_json FROM recognition_jobs').fetchall()
        assert con.execute('SELECT COUNT(*) FROM danzhengtong_jobs').fetchone()[0] == (2 if 'danzhengtong' in providers else 0)
    for row in queued:
        options = json.loads(row['payload_json'])
        plan = options['recognition_config']
        assert {method for stage in ('fields', 'products', 'handwriting', 'date', 'seal') for method in plan[stage]} == providers
        assert options['_sample_execution_mode'] == mode
        assert options['sample_options']['recognition_config'] == plan
    # A changed global or later batch plan must not broaden this saved scope.
    service.database.set_json_setting('recognition_defaults', DEFAULT_RECOGNITION_SETTINGS)
    service.store.recover()
    claimed = service.store.claim()
    assert json.loads(claimed['payload_json'])['_sample_execution_mode'] == mode
    assert json.loads(claimed['payload_json'])['recognition_config'] == json.loads(queued[0]['payload_json'])['recognition_config']
    assert all(item['execution_mode'] == mode for item in client.get('/api/samples').json['items'] if item['job_id'])
    assert all(row['execution_mode'] == mode for row in client.get('/api/samples/history').json)
    assert ctx.database.get_json_setting('recognition_defaults') == settings
    assert ctx.database.list_results() == []


@pytest.mark.parametrize('mode', ['local', 'both'])
def test_local_can_run_with_unconfigured_qingtong_in_saved_plan(samples, mode):
    ctx, client = samples
    settings = deepcopy(DEFAULT_RECOGNITION_SETTINGS)
    settings['recognition_config']['seal'] = ['paddle_v6', 'qingtong', 'danzhengtong']
    ctx.database.set_json_setting('recognition_defaults', settings)
    calls = []
    original = ctx._configured_analyze

    def analyze(*args, **options):
        calls.append(options['recognition_config'])
        return original(*args, **options)

    ctx._configured_analyze = analyze
    assert client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': mode}).status_code == 202
    service = SampleCenter(ctx)
    job = service.store.claim()
    result, preview = service.worker.execute(job)
    service.store.finish(job, result, preview)
    assert len(calls) == 1
    assert calls[0]['seal'] == ['paddle_v6']
    with service.database.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM danzhengtong_jobs').fetchone()[0] == int(mode == 'both')
    assert client.get('/api/samples').json['items'][0]['status'] == ('测试中' if mode == 'both' else '已完成')


def test_qingtong_executes_only_seal_stage_with_existing_sample_fields(samples):
    ctx, client = samples
    ctx.analyzer.seal_api.enabled = True
    calls = []

    def run_stage(context, stage, request):
        calls.append((stage, request.seal_mode, dict(request.fields)))
        return {'fields': dict(request.fields), 'seal_check': {
            'status': '匹配', 'reliable': True, 'recognized': '甲公司公章'}}

    analyzer = SimpleNamespace(seal_api=SimpleNamespace(enabled=True), run_stage=run_stage)
    def analyze(source, preview, **kwargs):
        return run_configured(analyzer, source, preview,
                              config=kwargs.pop('recognition_config'), **kwargs)

    ctx._configured_analyze = analyze
    assert client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'qingtong'}).status_code == 202
    service = SampleCenter(ctx)
    job = service.store.claim()
    result, preview = service.worker.execute(job)
    service.store.finish(job, result, preview)
    assert calls == [('seal', 'qingtong_only', {'客户名称': '甲公司', '承运商': '承运乙'})]
    assert result['recognition_status']['seal'] == '已执行'
    assert all(result['recognition_status'][stage] == '未执行' for stage in ('fields', 'date', 'handwriting', 'products'))
    assert not service.worker.remote.step()


def test_remote_only_uses_imported_original_and_finishes_while_regular_queue_paused(samples):
    ctx, client = samples
    assert upload(client, 'new.png').status_code == 201
    ctx._configured_analyze = lambda *args, **kwargs: pytest.fail('remote-only must not run local recognition')
    assert client.post('/api/samples/run', json={'filenames': ['new.png'], 'mode': 'danzhengtong'}).status_code == 202
    service = SampleCenter(ctx)
    service.store.set_paused(True)
    provider = FakeClient()
    seen = []
    original = provider.upload_file

    def uploaded(source, **kwargs):
        seen.append(source)
        assert source.is_file() and source.parent == service.library.directory
        return original(source, **kwargs)

    provider.upload_file = uploaded
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()
    item = next(row for row in client.get('/api/samples').json['items'] if row['filename'] == 'new.png')
    assert item['danzhengtong']['req_uuid'] == 'original-request'
    assert item['execution_mode'] == 'danzhengtong'
    # Reopening the runtime preserves both mode and the accepted remote request.
    reopened = SampleCenter(ctx)
    reopened.worker.remote.client_factory = lambda task: provider
    provider.ready = True
    reopened.store.remote_action([item['job_id']], 'query')
    for _ in range(8):
        if reopened.store.remote_detail(item['job_id'])['status'] == 'applied':
            break
        assert reopened.worker.remote.step()
    else:
        pytest.fail('the accepted remote-only sample did not finish')
    item = next(row for row in client.get('/api/samples').json['items'] if row['filename'] == 'new.png')
    assert item['status'] == '已完成' and item['danzhengtong']['status'] == 'applied'
    assert len(seen) == provider.uploads == provider.submits == 1
    assert ctx.database.list_results() == []


def test_pending_remote_sample_is_not_submitted_again_by_another_execution_choice(samples):
    ctx, client = samples
    first = client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'danzhengtong'})
    assert first.status_code == 202
    service = SampleCenter(ctx)
    provider = FakeClient()
    service.worker.remote.client_factory = lambda task: provider
    assert service.worker.remote.step()
    response = client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': 'local'})
    assert response.json['accepted'] == [] and response.json['skipped'] == ['one.png']
    assert len(client.get('/api/samples/history').json) == 1
    assert provider.uploads == provider.submits == 1


@pytest.mark.parametrize('mode', ['', 'other', [], True, 3])
def test_invalid_mode_creates_no_jobs(samples, mode):
    _, client = samples
    assert client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': mode}).status_code == 400
    assert client.get('/api/samples/history').json == []


@pytest.mark.parametrize('mode', ['qingtong', 'all'])
def test_qingtong_requires_configuration_before_any_job_is_created(samples, mode):
    _, client = samples
    response = client.post('/api/samples/run', json={'filenames': ['one.png'], 'mode': mode})
    assert response.status_code == 400
    assert '清瞳' in response.json['error']
    assert client.get('/api/samples/history').json == []
