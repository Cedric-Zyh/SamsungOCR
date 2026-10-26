"""Physical OCR accounting uses fake predictors and transports, never real OCR."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
import requests

from receipt_ocr import danzhengtong as dzt
from receipt_ocr.providers import orientation, paddle_runtime
from receipt_ocr.recognition.seal.providers import api
from receipt_ocr.runtime.calls import capture_recognition_calls, recognition_call
from receipt_ocr.runtime.execution import recognition_run, recognize_page, record_page_reuse
from receipt_ocr.runtime.progress import model_stage
from receipt_ocr.runtime.scope import provider_scope


@pytest.fixture
def source(tmp_path):
    path = tmp_path / '回单.png'
    Image.new('RGB', (80, 40), 'white').save(path)
    return path


def test_nested_capture_parallel_calls_and_safe_image_metadata(source, tmp_path):
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    crop = artifacts / '印章 1.png'
    crop.write_bytes(source.read_bytes())
    outside = artifacts / 'outside.png'
    outside.symlink_to(source)

    def run(index):
        with model_stage('seal', 'paddle_v6'):
            with recognition_call('paddle_v6', '文字检测与识别', crop):
                with recognition_call('paddle_v6', '嵌套包装', crop):
                    pass

    with capture_recognition_calls(source, artifacts, '/files/artifacts/run') as captured:
        with capture_recognition_calls() as nested:
            assert nested is captured
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda context: context.run(run, 0), [copy_context() for _ in range(12)]))
        with recognition_call('paddle_v6', '整页识别', source):
            pass
        with recognition_call('paddle_v6', '链接越界', outside):
            pass
    snapshot = captured.snapshot()
    assert snapshot['status'] == 'completed'
    assert snapshot['finished_at'] and snapshot['processing_seconds'] >= 0
    assert snapshot['summary'] == dict(total=14, succeeded=14, failed=0, cache_hits=0)
    assert [row['id'] for row in snapshot['calls']] == list(range(1, 15))
    row = snapshot['calls'][0]
    assert (row['stage'], row['stage_label'], row['input_name'], row['width'], row['height']) == (
        'seal', '客户印章', '印章 1.png', 80, 40)
    assert row['image_url'].startswith('/files/artifacts/run/')
    assert 'image_url' not in snapshot['calls'][-1]
    assert 'image_url' not in snapshot['calls'][-2]
    assert str(tmp_path) not in json.dumps(snapshot, ensure_ascii=False)
    snapshot['calls'].clear()
    assert len(captured.snapshot()['calls']) == 14
    with capture_recognition_calls() as next_run:
        pass
    assert next_run.snapshot()['summary']['total'] == 0


def test_failure_snapshot_attaches_to_exception_without_secrets(source):
    with pytest.raises(RuntimeError) as raised:
        with capture_recognition_calls(source):
            with recognition_call('qingtong', '印章识别', source):
                raise RuntimeError('api_key=secret /Users/private https://private/?signed=secret')
    snapshot = raised.value.recognition_calls
    assert snapshot['status'] == 'failed'
    assert snapshot['summary']['failed'] == 1
    assert snapshot['calls'][0]['status'] == 'failed'
    assert 'secret' not in json.dumps(snapshot)
    assert 'RuntimeError' in snapshot['calls'][0]['error']
    with capture_recognition_calls() as fresh:
        pass
    assert fresh.snapshot()['summary']['total'] == 0


@pytest.fixture
def predictions(monkeypatch):
    seen = []
    state = SimpleNamespace(error=False, raises=False)
    class Predictor:
        def predict(self, *, input, **kwargs):
            seen.append(input)
            if state.raises:
                raise RuntimeError('secret provider diagnostic')
            if state.error:
                return [{'error': 'secret provider diagnostic'}]
            return [{'rec_text': '测试', 'rec_score': .95,
                     'rec_texts': ['测试'], 'rec_scores': [.95],
                     'rec_polys': [[[1, 1], [60, 1], [60, 30], [1, 30]]],
                     'label_names': ['0'], 'scores': [.9]}]
    predictor = Predictor()
    monkeypatch.setattr(paddle_runtime, '_pipeline', lambda _: predictor)
    monkeypatch.setattr(paddle_runtime, '_line_recognizer', lambda _: predictor)
    monkeypatch.setattr(paddle_runtime, '_seal_pipeline', lambda: predictor)
    monkeypatch.setattr(paddle_runtime, 'paddle_engine', lambda: 'onnxruntime')
    monkeypatch.setattr(orientation, '_doc_classifier', predictor)
    monkeypatch.setattr(orientation, '_classifier', predictor)
    return seen, state


def test_actual_model_inputs_count_and_cache_reuse_does_not(source, predictions):
    seen, _ = predictions
    @recognition_run
    def run():
        with model_stage('fields', 'paddle_v6'):
            recognize_page(source, 'paddle_v6', lambda source, **_: paddle_runtime.recognize_text(source))
            recognize_page(source, 'paddle_v6', lambda *_args, **_kwargs: pytest.fail('cache miss'))
            record_page_reuse()
        with model_stage('seal', 'paddle_v6'):
            paddle_runtime.detect_text_boxes(source)
            paddle_runtime.detect_text_boxes(source)
            paddle_runtime.recognize_line(source)
            paddle_runtime.recognize_seal_text(source)
            orientation.classify_doc_orientation(source)
        return {}
    with capture_recognition_calls(source) as captured:
        run()
    snapshot = captured.snapshot()
    assert len(seen) == snapshot['summary']['total'] == 5
    assert snapshot['summary']['cache_hits'] == 3
    assert [row['stage'] for row in snapshot['calls']] == ['fields', 'seal', 'seal', 'seal', 'seal']
    assert snapshot['calls'][-1]['action'] == '方向识别'
    assert snapshot['calls'][-1]['method'] == 'paddle_doc_ori'
    assert all(row['width'] == 80 and row['height'] == 40 for row in snapshot['calls'])


@pytest.mark.parametrize('function', [paddle_runtime.recognize_text, paddle_runtime.recognize_line,
                                     paddle_runtime.detect_text_boxes, paddle_runtime.recognize_seal_text,
                                     orientation.classify_doc_orientation])
def test_prediction_error_response_is_failed_call(source, predictions, function):
    _, state = predictions
    state.error = True
    with capture_recognition_calls(source) as captured:
        try:
            function(source)
        except RuntimeError:
            pass
    snapshot = captured.snapshot()
    assert snapshot['summary'] == dict(total=1, succeeded=0, failed=1, cache_hits=0)
    assert 'secret' not in json.dumps(snapshot)


def test_disabled_or_model_setup_failure_is_not_an_inference(source, predictions, monkeypatch):
    seen, _ = predictions
    with capture_recognition_calls(source) as captured, provider_scope([]):
        assert paddle_runtime.recognize_text(source) == []
        assert paddle_runtime.recognize_seal_text(source) == []
        orientation.classify_doc_orientation(source)
    assert captured.snapshot()['summary']['total'] == 0
    assert seen == []
    def failed_setup(_):
        raise RuntimeError('model setup failed')
    monkeypatch.setattr(paddle_runtime, '_pipeline', failed_setup)
    with capture_recognition_calls(source) as captured:
        with pytest.raises(RuntimeError):
            paddle_runtime.recognize_text(source)
    assert captured.snapshot()['summary']['total'] == 0


@pytest.mark.parametrize('outcome', ['ok', 'business_failure', 'network_failure', 'disabled'])
def test_qingtong_counts_real_posts_and_business_failures(source, monkeypatch, outcome):
    client = api.SealApiClient()
    client.api_key = '' if outcome == 'disabled' else 'secret'
    posts = []
    def post(*args, **kwargs):
        posts.append(args)
        if outcome == 'network_failure':
            raise requests.ConnectionError('api_key=secret')
        return SimpleNamespace(ok=True, status_code=200,
                               json=lambda: {'code': 200 if outcome == 'ok' else 500})
    monkeypatch.setattr(api.requests, 'post', post)
    with capture_recognition_calls(source) as captured:
        client.recognize(source)
    snapshot = captured.snapshot()
    assert snapshot['summary']['total'] == len(posts) == (outcome != 'disabled')
    assert snapshot['summary']['failed'] == (outcome in {'business_failure', 'network_failure'})
    assert 'secret' not in json.dumps(snapshot)


@pytest.mark.parametrize('outcome', ['ok', 'submit_failure', 'query_failure', 'upload_failure'])
def test_danzhengtong_upload_and_polling_are_not_extra_recognition_calls(source, monkeypatch, outcome):
    from receipt_ocr.runtime import calls
    clock = [0.0]
    monkeypatch.setattr(calls, 'perf_counter', lambda: clock[0])
    requests_seen = []
    def response(body):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    def request(method, url, **kwargs):
        requests_seen.append((method, 'files' in kwargs))
        clock[0] += 10 if 'files' in kwargs else (2 if method == 'POST' else 3)
        if 'files' in kwargs:
            if outcome == 'upload_failure':
                raise RuntimeError('secret-upload-key')
            return response({'status': True, 'fileId': 'uploaded'})
        if method == 'POST':
            return response({'code': 500 if outcome == 'submit_failure' else 200,
                             'status': True, 'data': {'reqUuid': 'accepted'}})
        if outcome == 'query_failure':
            raise RuntimeError('secret-query-key')
        return response({'code': 200, 'data': {'commitResult':
                        {} if len(requests_seen) == 3 else {'客户名称': {'value': '测试客户'}}}})
    settings = dzt.Settings(app_id='id', app_key='key', app_secret='secret', key_id='gateway',
                            callback_url='https://example.com/notify')
    client = dzt.Client(settings, transport=SimpleNamespace(request=request))
    monkeypatch.setattr(dzt.time, 'sleep', lambda _: None)
    with capture_recognition_calls(source) as captured:
        try:
            client.recognize(source)
        except RuntimeError:
            pass
        if outcome == 'ok':
            # Looking up an existing accepted request must not count as a new recognition.
            client.get_result('accepted')
    snapshot = captured.snapshot()
    assert snapshot['summary']['total'] == (outcome != 'upload_failure')
    assert snapshot['summary']['failed'] == (outcome in {'submit_failure', 'query_failure'})
    assert 'secret' not in json.dumps(snapshot)
    if outcome == 'ok':
        assert len(requests_seen) == 5
        assert snapshot['calls'][0]['width'] == 80
        # Two result polls belong to this one recognition, upload and later manual GET do not.
        assert snapshot['calls'][0]['seconds'] == 8
        assert dzt._call_handoff.get() is None


def test_inference_duration_excludes_model_loading(source, monkeypatch):
    from receipt_ocr.runtime import calls
    clock = [0.0]
    monkeypatch.setattr(calls, 'perf_counter', lambda: clock[0])
    def predict(**kwargs):
        clock[0] += 2
        return []
    def setup(_):
        clock[0] += 100
        return SimpleNamespace(predict=predict)
    monkeypatch.setattr(paddle_runtime, '_pipeline', setup)
    with capture_recognition_calls(source) as captured:
        paddle_runtime.recognize_text(source)
    snapshot = captured.snapshot()
    assert snapshot['processing_seconds'] == 102
    assert snapshot['calls'][0]['seconds'] == 2


def test_actual_predict_exception_is_counted(source, predictions):
    seen, state = predictions
    state.raises = True
    with pytest.raises(RuntimeError) as raised:
        with capture_recognition_calls(source):
            paddle_runtime.recognize_text(source)
    assert len(seen) == 1
    assert raised.value.recognition_calls['summary']['failed'] == 1


def test_direct_danzhengtong_submission_without_credentials_does_not_count():
    client = dzt.Client(dzt.Settings())
    with capture_recognition_calls() as captured:
        with pytest.raises(ValueError):
            client.submit('receipt.png', 'file-id')
    assert captured.snapshot()['summary']['total'] == 0
