"""HTTP timing is measured locally; server processing time remains independent."""
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from receipt_ocr.recognition.seal.providers import api
from receipt_ocr.runtime.calls import capture_recognition_calls


@pytest.fixture
def client(tmp_path, monkeypatch):
    image = tmp_path / 'receipt.png'
    Image.new('RGB', (20, 30), 'white').save(image)
    monkeypatch.setenv('SEAL_API_KEY', 'test-key')
    monkeypatch.setenv('SEAL_API_KEY_FILE', str(tmp_path / 'missing-key'))
    clock = SimpleNamespace(seconds=10.0)
    monkeypatch.setattr(api, 'perf_counter', lambda: clock.seconds)

    def forbidden(*args, **kwargs):
        pytest.fail('this test must not perform a real request')

    monkeypatch.setattr(api.requests, 'post', forbidden)
    return api.SealApiClient(), image, clock


def response(payload, *, ok=True, status=200, decode=None):
    def json():
        if decode:
            decode()
        return payload
    return SimpleNamespace(ok=ok, status_code=status, json=json)


@pytest.mark.parametrize('ok,status,code,call_status', [
    (True, 200, 200, 'succeeded'),
    (True, 200, 503, 'failed'),
    (False, 503, 503, 'failed'),
])
def test_qingtong_measures_upload_http_but_excludes_local_json_decode(client, monkeypatch, ok, status, code, call_status):
    provider, image, clock = client
    payload = {'code': code, 'processing_time': .375, 'data': {}}
    uploads = []

    def decode():
        clock.seconds += 100  # Local response parsing is outside the HTTP scope.

    def post(url, **kwargs):
        uploads.append(kwargs['files']['files'][1].read())
        clock.seconds += 2.5
        return response(payload, ok=ok, status=status, decode=decode)

    monkeypatch.setattr(api.requests, 'post', post)
    with capture_recognition_calls(image) as captured:
        result = provider.recognize(image)
    assert uploads == [image.read_bytes()]
    assert result['timing'] == {'request_seconds': 2.5, 'server_seconds': .375}
    assert result['response'] is payload
    assert result['ok'] is (ok and code == 200)
    calls = captured.snapshot()['calls']
    assert len(calls) == 1
    assert calls[0]['method'] == 'qingtong' and calls[0]['status'] == call_status
    # The existing physical-call timer still owns its measured duration.
    assert isinstance(calls[0]['seconds'], float) and calls[0]['seconds'] >= 0


@pytest.mark.parametrize('error', [requests.Timeout, requests.ConnectionError, OSError])
def test_failed_http_attempt_keeps_measured_time_without_server_time(client, monkeypatch, error):
    provider, image, clock = client

    def post(*args, **kwargs):
        clock.seconds += 1.25
        raise error('fake transport failure')

    monkeypatch.setattr(api.requests, 'post', post)
    with capture_recognition_calls(image) as captured:
        result = provider.recognize(image)
    assert result['ok'] is False
    assert result['timing'] == {'request_seconds': 1.25, 'server_seconds': None}
    assert captured.snapshot()['summary']['total'] == captured.snapshot()['summary']['failed'] == 1


@pytest.mark.parametrize('value', [None, '', '0.42', True, False, -.1, float('inf'), float('nan'), 10**1000, {}, []])
def test_server_time_rejects_missing_non_numeric_and_invalid_values(client, monkeypatch, value):
    provider, image, clock = client

    def post(*args, **kwargs):
        clock.seconds += .5
        return response({'code': 200, 'processing_time': value})

    monkeypatch.setattr(api.requests, 'post', post)
    assert provider.recognize(image)['timing'] == {'request_seconds': .5, 'server_seconds': None}


@pytest.mark.parametrize('value', [0, 1, .42])
def test_server_time_accepts_only_explicit_nonnegative_json_number(client, monkeypatch, value):
    provider, image, clock = client

    def post(*args, **kwargs):
        clock.seconds += .75
        return response({'code': 200, 'processing_time': value})

    monkeypatch.setattr(api.requests, 'post', post)
    assert provider.recognize(image)['timing'] == {'request_seconds': .75, 'server_seconds': value}


@pytest.mark.parametrize('mode', ['disabled', 'missing_file', 'skipped'])
def test_unattempted_requests_do_not_invent_zero_duration(client, mode):
    provider, image, _ = client
    if mode == 'disabled':
        provider.api_key = ''
    elif mode == 'missing_file':
        image = image.with_name('missing.png')
    with capture_recognition_calls() as captured:
        result = provider.skipped() if mode == 'skipped' else provider.recognize(image)
    assert 'timing' not in result
    assert captured.snapshot()['calls'] == []


def test_invalid_json_keeps_http_duration_without_server_time(client, monkeypatch):
    provider, image, clock = client

    def decode():
        raise ValueError('not JSON')

    def post(*args, **kwargs):
        clock.seconds += .25
        return SimpleNamespace(ok=True, status_code=200, text='invalid response', json=decode)

    monkeypatch.setattr(api.requests, 'post', post)
    result = provider.recognize(image)
    assert result['ok'] is False
    assert result['timing'] == {'request_seconds': .25, 'server_seconds': None}
