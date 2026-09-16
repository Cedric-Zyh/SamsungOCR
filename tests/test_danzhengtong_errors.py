from types import SimpleNamespace

import pytest
import requests

from receipt_ocr.danzhengtong import Client, Settings


def settings():
    return Settings(app_id='test-app-id', app_key='test-app-key', app_secret='test-app-secret',
                    key_id='test-gateway-key', callback_url='https://example.com/callback')


def reply(body, status=200, text=''):
    def raise_status():
        if status >= 400:
            raise requests.HTTPError(f'{status} Server Error')
    def read_json():
        if isinstance(body, Exception):
            raise body
        return body
    return SimpleNamespace(status_code=status, raise_for_status=raise_status, json=read_json, text=text)


@pytest.mark.parametrize('error', [requests.ConnectTimeout('connection timed out'),
                                  requests.ConnectionError('NameResolutionError: name lookup failed')])
def test_upload_connection_failure_keeps_actual_exception(tmp_path, error):
    source = tmp_path / 'sample.jpg'; source.write_bytes(b'image')
    def request(*a, **kw):
        raise error
    client = Client(settings(), SimpleNamespace(request=request))
    with pytest.raises(RuntimeError) as found:
        client.upload_file(source)
    assert '单证通文件上传失败' in str(found.value)
    assert type(error).__name__ in str(found.value)
    assert str(error) in str(found.value)


@pytest.mark.parametrize('method,stage', [('POST', '提交识别'), ('GET', '查询结果')])
def test_gateway_http_failures_keep_stage_status_code_and_provider_message(method, stage):
    client = Client(settings(), SimpleNamespace(request=lambda *a, **kw: reply(
        {'code': 'RATE_LIMIT', 'message': '请求过于频繁，请稍后重试'}, 429)))
    with pytest.raises(RuntimeError) as found:
        client._request(method, '/test')
    message = str(found.value)
    assert f'单证通{stage}失败' in message
    assert 'HTTP 429' in message and 'code=RATE_LIMIT' in message
    assert '请求过于频繁，请稍后重试' in message


def test_business_upload_error_keeps_server_message(tmp_path):
    source = tmp_path / 'sample.jpg'; source.write_bytes(b'image')
    client = Client(settings(), SimpleNamespace(request=lambda *a, **kw: reply(
        {'status': False, 'code': 'INVALID_SOURCE', 'message': 'source_code is null'})))
    with pytest.raises(RuntimeError, match='source_code is null') as found:
        client.upload_file(source)
    assert 'code=INVALID_SOURCE' in str(found.value)
    assert 'HTTP 200' in str(found.value)


def test_callback_failure_preserves_provider_message():
    client = Client(settings())
    client.jobs['request-1'] = {'status': 'submitted'}
    with pytest.raises(ValueError) as found:
        client.receive_callback({'code': 500, 'success': False, 'message': '识别模型处理失败',
                                 'data': {'reqUuid': 'request-1', 'status': 2}})
    assert '识别模型处理失败' in str(found.value)
    assert 'code=500' in str(found.value)


def test_non_json_response_retains_status_and_text(tmp_path):
    source = tmp_path / 'sample.jpg'; source.write_bytes(b'image')
    client = Client(settings(), SimpleNamespace(request=lambda *a, **kw: reply(
        ValueError('Expecting value: line 1 column 1'), 200, '<html>Gateway temporarily unavailable</html>')))
    with pytest.raises(RuntimeError) as found:
        client.upload_file(source)
    assert 'HTTP 200' in str(found.value)
    assert 'Expecting value' in str(found.value)
    assert 'Gateway temporarily unavailable' in str(found.value)


def test_actual_errors_redact_credentials_and_signed_url_parameters():
    cfg = settings()
    def request(*a, **kw):
        raise requests.ConnectionError(f'connection rejected {cfg.app_secret} {cfg.app_key} '
                                       f'{cfg.key_id} {cfg.app_id} https://example.com/file?signature=secret-query')
    client = Client(cfg, SimpleNamespace(request=request))
    with pytest.raises(RuntimeError) as found:
        client._request('POST', '/test')
    text = str(found.value)
    assert 'connection rejected' in text
    for secret in (cfg.app_secret, cfg.app_key, cfg.key_id, cfg.app_id, 'secret-query'):
        assert secret not in text


def test_stage_failures_survive_missing_comparison_requirements(monkeypatch):
    from receipt_ocr import danzhengtong
    from receipt_ocr.recognition_config import run_configured
    def fail(*a, **kw):
        raise RuntimeError('单证通文件上传失败：HTTP 503；文件服务不可用')
    monkeypatch.setattr(danzhengtong, 'stage_result', fail)
    engine = SimpleNamespace(seal_api=SimpleNamespace(enabled=True), run_stage=lambda *a, **kw:
                             {'seal_check': {'status': '缺少比对依据', 'reliable': False}})
    result = run_configured(engine, 'unused.jpg', None,
                            config={'fields': ['danzhengtong'], 'date': ['danzhengtong'], 'seal': ['qingtong']})
    assert result['date_check']['status'] == '识别失败'
    assert 'HTTP 503' in result['date_check']['message']
    assert result['recognition_variants']['fields'][0]['error'].endswith('文件服务不可用')
