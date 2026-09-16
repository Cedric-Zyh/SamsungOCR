from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from receipt_ocr.danzhengtong import Client, Settings, MOCK_NOTICE
from receipt_ocr.recognition_config import run_configured


def test_mock_complete_without_network_and_redacts_credentials(monkeypatch):
    import requests
    def forbidden(*a, **kw):
        raise AssertionError('unexpected network')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    client = Client(Settings(mode='mock', app_id='id-secret', app_key='key-secret', app_secret='secret-value'))
    result, trace = client.recognize_mock('sample.png')
    assert trace['status'] == 'completed'
    assert trace['upload']['simulated']
    assert trace['upload']['response']['fileId'] == trace['request']['files'][0]['fileId']
    assert 'callback' not in trace
    assert trace['query'] == {'delay_seconds': 2, 'strategy': 'fixed_delay', 'status': 'received'}
    assert result['fields']['实收数量'] == '10'
    assert trace['request']['orgId'] == '101517'
    assert trace['request']['sysCode'] == 'LOGISTICS_SAMSUNG'
    assert 'secret-value' not in json.dumps(trace)
    assert 'key-secret' not in json.dumps(trace)


def test_callback_correlation_failure_and_duplicate():
    client = Client(Settings(mode='mock'))
    req = client.submit('x.png', 'mock-file')['data']['reqUuid']
    assert client.get_result(req)['simulated']
    with pytest.raises(ValueError):
        client.receive_callback({'data': {'reqUuid': 'unknown'}})
    callback = {'code': 200, 'success': True, 'data': {'reqUuid': req, 'status': 1}}
    assert client.receive_callback(callback) == req
    assert client.receive_callback(callback) == req
    assert client.get_result(req)['simulated']
    with pytest.raises(ValueError):
        client.receive_callback({**callback, 'success': False})
    with pytest.raises(RuntimeError):
        client.get_result(req)


def test_real_transport_contract_and_configuration_guard():
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {'code': 200, 'status': True, 'data': {'reqUuid': 'real-uuid'}})
    transport = SimpleNamespace(request=request)
    client = Client(Settings(mode='real'), transport=transport)
    with pytest.raises(ValueError, match='配置缺失'):
        client.submit('x.png', 'uploaded-id')
    assert not calls
    client.settings = replace(client.settings, key_id='gateway', app_id='id', app_key='key', app_secret='secret', callback_url='https://example.com/callback')
    client.submit('x.png', 'uploaded-id')
    client.get_result('real-uuid')
    assert calls[0][0] == 'POST'
    assert calls[0][2]['json']['files'][0]['fileId'] == 'uploaded-id'
    assert calls[0][2]['headers']['keyId'] == 'gateway'
    assert calls[1][0] == 'GET'
    assert calls[1][2]['params'] == {'reqUuid': 'real-uuid'}
    with pytest.raises(RuntimeError, match='待联调'):
        client.recognize_mock('x.png')


def test_pipeline_shares_one_request_and_keeps_mock_reviewable(monkeypatch, tmp_path):
    from receipt_ocr import danzhengtong, document_context
    from PIL import Image
    monkeypatch.setattr(Settings, 'load', classmethod(lambda cls: Settings(mode='mock')))
    def forbidden(*a, **kw):
        raise AssertionError('local OCR must not execute')
    monkeypatch.setattr(document_context.DocumentContext, 'page', forbidden)
    original = danzhengtong.Client.submit
    calls = []
    def submit(self, *args):
        calls.append(args)
        return original(self, *args)
    monkeypatch.setattr(Client, 'submit', submit)
    source = tmp_path / 'sample.png'
    preview = tmp_path / 'preview.png'
    Image.new('RGB', (100, 100), 'white').save(source)
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(enabled=False), run_stage=forbidden)
    result = run_configured(analyzer, source, preview, config={s: ['danzhengtong'] for s in ('fields','products','handwriting','date')})
    assert len(calls) == 1
    assert preview.exists()
    assert result['fields']['实收数量'] == '10'
    assert result['fields']['拒收数量'] == '0'
    assert result['fields']['签收日期'] == '2026-09-10'
    assert result['danzhengtong']['simulated']
    assert result['overall'] == '需人工复核'
    assert MOCK_NOTICE in result['review_reasons']
    assert all(result['recognition_status'][s] == '已执行' for s in ('fields','products','handwriting','date'))


def test_web_option_and_saved_trace(monkeypatch, tmp_path):
    import app as web
    from receipt_ocr.database import Database
    monkeypatch.setattr(web, 'default_backend', lambda: 'vision')
    page = web.app.test_client().get('/')
    assert page.status_code == 200
    assert page.get_data(as_text=True).count('data-method="danzhengtong"') == 4
    fixture, trace = Client(Settings(mode='mock')).recognize_mock('sample.png')
    database = Database(tmp_path / 'trace.db')
    database.initialize()
    result = {'filename': 'sample.png', 'overall': '需人工复核', 'fields': fixture['fields'], 'danzhengtong': trace}
    result_id = database.insert_result(filename='sample.png', stored_name='sample.png',
                                       preview_name='preview.png', task_id='', result=result)
    restored = database.get_result(result_id)
    assert restored['danzhengtong'] == trace
    assert restored['fields']['实收数量'] == '10'


def test_upload_multipart_then_submit_uses_returned_id(tmp_path):
    source = tmp_path / 'stored.JPG'
    source.write_bytes(b'example-image')
    calls = []
    streams = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if 'files' in kwargs:
            name, stream, mime = kwargs['files']['file']
            streams.append(stream)
            assert name == '原始送货单.jpg'
            assert stream.read() == b'example-image'
            assert mime == 'image/jpeg'
            assert 'headers' not in kwargs
            assert kwargs['data'] == {'org_id': '101517', 'source_code': 'LOGISTICS_SAMSUNG',
                                      'file_type': 'jpg', 'file_name': name, 'file_is_outer_visible': 'N'}
            body = {'status': True, 'fileId': 'returned-file-id', 'data': 'fallback-id',
                    'filePath': 'https://example.com/private?signature=secret'}
        else:
            assert kwargs['json']['files'] == [{'fileName': '原始送货单.jpg', 'fileId': 'returned-file-id'}]
            body = {'code': 200, 'status': True, 'data': {'reqUuid': 'request-id'}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    settings = Settings(mode='real', key_id='gateway', app_id='id', app_key='key',
                        app_secret='secret', callback_url='https://example.com/callback')
    client = Client(settings, transport=SimpleNamespace(request=request))
    client.submit_file(source, file_name='原始送货单.jpg')
    assert len(calls) == 2
    assert calls[0][1] == settings.upload_url
    assert streams[0].closed
    assert 'signature' not in json.dumps(client.jobs)


@pytest.mark.parametrize('body', [{'status': False, 'message': 'source_code is null'},
                                  {'status': True}, {'status': True, 'data': {}}, []])
def test_failed_upload_does_not_submit(tmp_path, body):
    source = tmp_path / 'sample.png'
    source.write_bytes(b'image')
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    settings = Settings(mode='real', key_id='gateway', app_id='id', app_key='key',
                        app_secret='secret', callback_url='https://example.com/callback')
    client = Client(settings, transport=SimpleNamespace(request=request))
    with pytest.raises(RuntimeError):
        client.submit_file(source)
    assert len(calls) == 1
    assert not client.jobs


def test_upload_accepts_data_fallback_and_source_override(tmp_path):
    source = tmp_path / 'sample.pdf'
    source.write_bytes(b'pdf')
    def request(*args, **kwargs):
        assert kwargs['data']['source_code'] == '12345'
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {'status': True, 'data': 'legacy-file-id'})
    client = Client(Settings(mode='real', upload_source_code='12345'), transport=SimpleNamespace(request=request))
    assert client.upload_file(source)['fileId'] == 'legacy-file-id'


def test_query_waits_two_seconds_without_callback(monkeypatch):
    client = Client(Settings(mode='mock'))
    req = client.submit('sample.png', 'mock-file')['data']['reqUuid']
    events = []
    monkeypatch.setattr('receipt_ocr.danzhengtong.time.sleep', lambda seconds: events.append(('sleep', seconds)))
    def query(value):
        events.append(('query', value))
        return {'pending': True}
    def forbidden(*args):
        raise AssertionError('must not wait for callback')
    monkeypatch.setattr(client, 'get_result', query)
    monkeypatch.setattr(client, 'receive_callback', forbidden)
    assert client.query_after_delay(req) == {'pending': True}
    assert events == [('sleep', 2), ('query', req)]
    assert client.jobs[req]['status'] == 'submitted'


def test_delayed_query_failure_is_not_completion(monkeypatch):
    client = Client(Settings(mode='mock'))
    req = client.submit('sample.png', 'mock-file')['data']['reqUuid']
    monkeypatch.setattr('receipt_ocr.danzhengtong.time.sleep', lambda seconds: None)
    def fail(value):
        raise RuntimeError('not ready')
    monkeypatch.setattr(client, 'get_result', fail)
    with pytest.raises(RuntimeError, match='not ready'):
        client.query_after_delay(req)
    assert client.jobs[req]['query']['status'] == 'failed'
    assert client.jobs[req]['status'] != 'completed'
