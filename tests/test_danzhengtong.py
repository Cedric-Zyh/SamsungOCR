from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from receipt_ocr.danzhengtong import Client, Settings


def test_callback_correlation_failure_and_duplicate():
    client = Client(Settings())
    client.jobs['request-id'] = {'status': 'submitted'}
    with pytest.raises(ValueError):
        client.receive_callback({'data': {'reqUuid': 'unknown'}})
    callback = {'code': 200, 'success': True, 'data': {'reqUuid': 'request-id', 'status': 1}}
    assert client.receive_callback(callback) == 'request-id'
    assert client.receive_callback(callback) == 'request-id'
    with pytest.raises(ValueError):
        client.receive_callback({**callback, 'success': False})
    assert client.jobs['request-id']['status'] == 'failed'


def test_web_options_and_saved_trace(tmp_path):
    from receipt_ocr.web import application as web
    from receipt_ocr.persistence.database import Database
    page = web.app.test_client().get('/')
    assert page.status_code == 200
    assert page.get_data(as_text=True).count('data-method="danzhengtong"') == 4
    trace = {'mode': 'real', 'simulated': False, 'status': 'completed', 'reqUuid': 'request-id'}
    database = Database(tmp_path / 'trace.db')
    database.initialize()
    result = {'filename': 'sample.png', 'overall': '需人工复核',
              'fields': {'实收数量': '10'}, 'danzhengtong': trace}
    result_id = database.insert_result(filename='sample.png', stored_name='sample.png',
                                       preview_name='preview.png', task_id='', result=result)
    restored = database.get_result(result_id)
    assert restored['danzhengtong'] == trace
    assert restored['fields']['实收数量'] == '10'


@pytest.mark.parametrize('source', ['environment', 'file'])
def test_removed_mode_fails_before_any_upload(source, tmp_path, monkeypatch):
    from receipt_ocr import danzhengtong
    monkeypatch.setattr(danzhengtong, 'ROOT', tmp_path)
    monkeypatch.delenv('DZT_MODE', raising=False)
    if source == 'environment':
        monkeypatch.setenv('DZT_MODE', 'mock')
    else:
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config' / 'danzhengtong.local.json').write_text('{"mode":"mock"}')
    with pytest.raises(ValueError, match='已移除模拟模式'):
        Client()


def test_real_transport_contract_and_configuration_guard():
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {'code': 200, 'status': True, 'data': {'reqUuid': 'real-uuid'}})
    transport = SimpleNamespace(request=request)
    client = Client(Settings(), transport=transport)
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
    trace = json.dumps(client.jobs)
    assert 'secret' not in trace
    assert client.jobs['real-uuid']['request']['appKey'] == '***'


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
    settings = Settings(key_id='gateway', app_id='id', app_key='key',
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
    settings = Settings(key_id='gateway', app_id='id', app_key='key',
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
    client = Client(Settings(upload_source_code='12345'), transport=SimpleNamespace(request=request))
    assert client.upload_file(source)['fileId'] == 'legacy-file-id'
