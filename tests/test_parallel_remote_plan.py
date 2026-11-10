"""Physical remote requests overlap routing; comparisons use final main-thread fields."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from receipt_ocr import danzhengtong as dzt
from receipt_ocr import danzhengtong_seal as dzt_seal
from receipt_ocr.application import plans
from receipt_ocr.domain.stages import STAGES
from receipt_ocr.recognition.seal.providers.api import SealApiClient
from receipt_ocr.runtime.calls import capture_recognition_calls, recognition_call
from receipt_ocr.runtime.progress import current_progress_context, progress_reporting
from receipt_ocr.runtime.scope import allowed_providers, provider_scope


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'receipt.png'
    Image.new('RGB', (100, 70), 'white').save(path)
    return path


def plan(**stages):
    return {**dict.fromkeys(STAGES, []), **stages}


def context(source, page=None, allows=True):
    ctx = SimpleNamespace(source=source, filename=source.name, primary_backend='paddle_v6',
                          document_type={'type': 'unclassified', 'reliable': False},
                          allows_remote_seal=allows, has_footer=True)
    ctx.page = page or (lambda backend: [])
    ctx.evidence = lambda: {'document_type': deepcopy(ctx.document_type)}
    return ctx


def qingtong_client():
    client = SealApiClient.__new__(SealApiClient)
    client.base_url, client.api_key, client.timeout = 'https://offline.example', 'test-only', 1
    return client


def test_qingtong_http_starts_before_page_finishes_and_reuses_one_result_for_both_seal_readers(source, monkeypatch):
    http_started, page_finished, fields_ready = Event(), Event(), Event()
    post_calls, progress, seen_evidence, compared_fields = [], [], [], []
    observed = {}

    def post(url, **kwargs):
        with kwargs['files']['files'][1] as image:
            assert image.read() == source.read_bytes()
        post_calls.append((url, current_progress_context(), allowed_providers()))
        http_started.set()
        assert page_finished.wait(2)
        assert fields_ready.wait(2)
        return SimpleNamespace(ok=True, status_code=200, json=lambda: {'code': 200, 'data': []})

    monkeypatch.setattr(requests, 'post', post)
    ctx = context(source)

    def page(backend):
        with recognition_call('paddle_v6', '整页识别', source):
            observed['http_started_before_page_finished'] = http_started.wait(1)
            ctx.document_type.update(type='receipt', reliable=True)
            page_finished.set()
        return []

    ctx.page = page

    def run_stage(ctx, stage, request):
        if stage == 'fields':
            assert page_finished.is_set()
            observed['local_fields_ran_before_remote_return'] = not fields_ready.is_set()
            fields_ready.set()
            return {'fields': {'签章要求': '本次正确客户收货章'}}
        assert stage == 'seal' and fields_ready.is_set()
        seen_evidence.append(request.seal_evidence)
        compared_fields.append(dict(request.fields))
        return {'seal_check': {'api': request.seal_evidence.require_response(), 'status': '匹配', 'reliable': True}}

    analyzer = SimpleNamespace(seal_api=qingtong_client(), run_stage=run_stage)
    with capture_recognition_calls(source) as captured, progress_reporting(progress.append), provider_scope({'paddle_v6'}):
        results, errors, _, fields = plans._recognize_stages(analyzer, ctx,
            plan(fields=['paddle_v6'], seal=['paddle_v6', 'qingtong']), {'签章要求': '旧单据要求'}, {})
    assert errors == [] and all(observed.values())
    assert len(post_calls) == 1 and seen_evidence[0] is seen_evidence[1]
    assert all(item['签章要求'] == fields['签章要求'] == '本次正确客户收货章' for item in compared_fields)
    assert post_calls[0][1]['method'] == 'qingtong' and post_calls[0][1]['channel'] == 'parallel'
    assert post_calls[0][2] == frozenset({'qingtong'})
    assert any(event.get('method') == 'qingtong' and event.get('channel') == 'parallel' for event in progress)
    physical = captured.snapshot()['calls']
    qt, = [call for call in physical if call['method'] == 'qingtong']
    local, = [call for call in physical if call['method'] == 'paddle_v6']
    assert datetime.fromisoformat(qt['started_at']) < datetime.fromisoformat(local['finished_at'])
    assert qt['stage'] == 'seal' and qt['run_id'] == local['run_id']
    assert all(variant['result']['seal_check']['api']['ok'] for variant in results['seal'])


def test_qingtong_network_failure_does_not_stop_local_routing_or_fields(source, monkeypatch):
    started = Event()
    calls = []

    def post(*args, **kwargs):
        calls.append('post')
        started.set()
        raise requests.ConnectionError('offline transport failure')

    monkeypatch.setattr(requests, 'post', post)
    ctx = context(source, page=lambda backend: started.wait(1))

    def run_stage(ctx, stage, request):
        if stage == 'fields':
            return {'fields': {'签章要求': '客户收货章'}}
        api = request.seal_evidence.require_response()
        return {'seal_check': {'status': '识别失败' if request.seal_mode == 'qingtong_only' else '匹配',
                               'recognized': '客户收货章', 'api': api}}

    results, errors, _, fields = plans._recognize_stages(
        SimpleNamespace(seal_api=qingtong_client(), run_stage=run_stage), ctx,
        plan(fields=['paddle_v6'], seal=['paddle_v6', 'qingtong']), {}, {})
    assert errors == [] and calls == ['post'] and fields['签章要求'] == '客户收货章'
    assert results['seal'][0]['result']['seal_check']['status'] == '匹配'
    assert results['seal'][1]['result']['seal_check']['status'] == '识别失败'


def test_known_rejected_context_does_not_start_qingtong_prefetch(source):
    def forbidden(*args):
        raise AssertionError('known rejected document must not upload')

    ctx = context(source, allows=False)
    seen = []
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(recognize=forbidden),
                              run_stage=lambda ctx, stage, request: seen.append(request.seal_evidence) or {'fields': {}})
    plans._recognize_stages(analyzer, ctx, plan(fields=['paddle_v6'], seal=['qingtong']), {}, {})
    assert seen == [None, None]


def test_prefetch_exception_is_reported_once_without_repeating_upload(source):
    entered, routed = Event(), Event()
    calls = []

    def recognize(path):
        calls.append(path)
        entered.set()
        assert routed.wait(2)
        raise RuntimeError('remote worker failed')

    def page(backend):
        assert entered.wait(1)
        routed.set()
        return []

    def run_stage(ctx, stage, request):
        if stage == 'fields':
            return {'fields': {'签章要求': '客户章'}}
        request.seal_evidence.require_response()

    results, errors, _, fields = plans._recognize_stages(
        SimpleNamespace(seal_api=SimpleNamespace(recognize=recognize), run_stage=run_stage),
        context(source, page), plan(fields=['paddle_v6'], seal=['qingtong']), {}, {})
    assert len(calls) == 1 and fields['签章要求'] == '客户章'
    assert len(errors) == 1 and errors[0]['provider'] == 'qingtong'
    assert results['seal'][0]['error'] == 'remote worker failed'


@pytest.mark.parametrize('page_fails', [False, True])
def test_dzt_page_and_seal_fetch_overlap_routing_but_compare_on_main_thread(source, tmp_path, monkeypatch, page_fails):
    main_thread = get_ident()
    page_started, seal_started, local_started = Event(), Event(), Event()
    fetches, comparisons, artifact_paths = [], [], []
    ctx = context(source)

    class Client:
        def recognize(self, path, *, file_name):
            assert get_ident() != main_thread and path == source
            fetches.append('page')
            page_started.set()
            assert seal_started.wait(2) and local_started.wait(2)
            if page_fails:
                raise RuntimeError('page scene failed')
            values = {name: {'value': value} for name, value in {
                '客户名称': '本次客户', '签章要求': '本次客户收货章',
                '要求到货': '2026-11-09', '签收日期': '2026-11-09'}.items()}
            return dzt.normalize_result(values), {'status': 'completed', 'mode': 'real', 'simulated': False,
                                                  'result': {'commitResult': values}}

    def fetch_requests(path, *, artifact_dir, artifact_url_prefix):
        assert get_ident() != main_thread and path == source
        fetches.append('seal')
        seal_started.set()
        assert page_started.wait(2) and local_started.wait(2)
        directory = Path(artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        input_path = directory / 'prepared.png'
        input_path.write_bytes(source.read_bytes())
        artifact_paths.append(input_path)
        return {'requests': [{'index': 0, 'status': 'completed', 'input_path': str(input_path),
                              'result': {'commitResult': {'收货客户印章': {'value': '本次客户收货章'}}}}],
                'trace': {'model_id': 'seal-scene', 'status': 'completed'}}

    def page(backend):
        assert get_ident() == main_thread
        local_started.set()
        assert page_started.wait(2) and seal_started.wait(2)
        ctx.document_type.update(type='receipt', reliable=True)
        return []

    ctx.page = page
    original_stage, original_build = dzt.stage_result, dzt_seal.build_stage_result

    def stage_result(context, stage, cache, fields):
        assert get_ident() == main_thread
        comparisons.append(('page', stage, deepcopy(fields)))
        return original_stage(context, stage, cache, fields)

    def build_stage_result(context, fields, requests, trace, **kwargs):
        assert get_ident() == main_thread and context is ctx
        assert fields['签章要求'] == '本次客户收货章'
        assert artifact_paths[0].is_file()
        comparisons.append(('seal', 'seal', deepcopy(fields)))
        return original_build(context, fields, requests, trace, **kwargs)

    monkeypatch.setattr(dzt, 'Client', Client)
    monkeypatch.setattr(dzt, 'stage_result', stage_result)
    monkeypatch.setattr(dzt_seal, 'fetch_requests', fetch_requests)
    monkeypatch.setattr(dzt_seal, 'build_stage_result', build_stage_result)
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(), run_stage=lambda context, stage, request:
                               {'fields': {'签章要求': '本次客户收货章', '要求到货': '2026-11-09'}})
    results, errors, _, fields = plans._recognize_stages(analyzer, ctx,
        plan(fields=['paddle_v6', 'danzhengtong'], date=['danzhengtong'], seal=['danzhengtong']),
        {'签章要求': '旧要求'}, {'artifact_dir': tmp_path / 'artifacts', 'artifact_url_prefix': '/files/test'})
    assert sorted(fetches) == ['page', 'seal']
    assert results['seal'][0]['result']['seal_check']['recognized'] == '本次客户收货章'
    assert results['seal'][0]['result']['seal_check']['status'] == '匹配'
    assert artifact_paths[0].parent.name == 'seal-danzhengtong'
    assert len(comparisons) == 3
    assert [error['scope'] for error in errors] == (['fields', 'date'] if page_fails else [])


def test_dzt_only_seal_prefetch_keeps_temporary_input_alive_until_main_projection(source, monkeypatch):
    main_thread = get_ident()
    path_seen = []

    def fetch(path, *, artifact_dir, artifact_url_prefix):
        input_path = Path(artifact_dir) / 'seal.png'
        input_path.write_bytes(source.read_bytes())
        path_seen.append(input_path)
        return {'requests': [], 'trace': {}}

    def build(context, fields, requests, trace, **kwargs):
        assert get_ident() == main_thread and path_seen[0].is_file()
        return {'seal_check': {'status': '未识别'}}

    monkeypatch.setattr(dzt_seal, 'fetch_requests', fetch)
    monkeypatch.setattr(dzt_seal, 'build_stage_result', build)
    monkeypatch.setattr(dzt, 'Client', lambda: pytest.fail('seal-only called page scene'))
    analyzer = SimpleNamespace(seal_api=SimpleNamespace())
    _, errors, _, _ = plans._recognize_stages(analyzer, context(source), plan(seal=['danzhengtong']), {}, {})
    assert errors == [] and not path_seen[0].exists()
