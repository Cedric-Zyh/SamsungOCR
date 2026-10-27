from threading import Event
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from PIL import Image

import pytest

from receipt_ocr.providers import model_manager as models, paddle_runtime as paddle, orientation
from receipt_ocr.application import RecognitionService
from test_job_queue import web_queue, store


PLAN = {'fields':['paddle_v6'], 'date':['paddle_v6'], 'seal':['paddle_v6','qingtong'],
        'seal_orientation':'combined'}


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(paddle, '_PIPELINES', {})
    monkeypatch.setattr(paddle, '_LINE_RECOGNIZERS', {})
    monkeypatch.setattr(paddle, '_SEAL_DETECTORS', {})
    monkeypatch.setattr(orientation, '_classifier', None)
    monkeypatch.setattr(orientation, '_doc_classifier', None)
    manager = models.LocalModelManager()
    monkeypatch.setattr(models, 'local_model_manager', manager)
    seen = []
    def load(key):
        seen.append(key)
        if key == 'page_v6': paddle._PIPELINES['v6'] = object()
        elif key == 'line_v6': paddle._LINE_RECOGNIZERS['v6'] = object()
        elif key == 'seal': paddle._SEAL_DETECTORS['seal'] = object()
        elif key == 'line_orientation': orientation._classifier = object()
        elif key == 'doc_orientation': orientation._doc_classifier = object()
    monkeypatch.setattr(models, '_load_model', load)
    manager.seen = seen
    yield manager
    if manager._thread is not None:
        manager._thread.join(timeout=5)
        assert not manager._thread.is_alive()


@pytest.mark.parametrize('plan,expected', [
    ({'fields':['paddle_v6']}, ['page_v6']),
    ({'seal':['qingtong'],'date':['danzhengtong']}, []),
    ({'seal':['paddle_seal']}, ['page_v6','line_v6','seal','line_orientation']),
    ({'seal':['paddle_v6'],'seal_orientation':'none'}, ['page_v6','line_v6']),
    (PLAN, ['page_v6','line_v6','line_orientation']),
    ({**PLAN,'seal_orientation':'doc_ori'}, ['page_v6','line_v6','line_orientation','doc_orientation']),
])
def test_only_current_local_routes_are_prepared(plan, expected):
    assert models.required_models(plan) == expected


def test_deliberately_enabled_local_audit_is_included_for_remote_seal_routes(monkeypatch):
    monkeypatch.setenv('SEAL_SECONDARY_READ_MODE', 'local')
    assert models.required_models({'seal':['qingtong']}) == ['page_v6','line_v6','line_orientation']
    monkeypatch.setenv('SEAL_SECONDARY_READ_MODE', 'off')
    assert models.required_models({'seal':['qingtong']}) == []


def test_load_once_release_all_and_allow_lazy_loading_again(manager):
    assert manager.status(PLAN)['state'] == 'unloaded'
    assert manager.seen == []
    assert manager.change(True, PLAN)['operation'] == 'loading'
    manager._thread.join(timeout=5)
    state = manager.status(PLAN)
    assert state['ready'] and state['loaded_count'] == 3
    assert set(manager.seen) == set(models.required_models(PLAN))
    assert state['initialization_seconds'] >= 0
    manager.change(True, PLAN)
    assert len(manager.seen) == 3
    # Also unload models retained from a previous plan.
    paddle._SEAL_DETECTORS['seal'] = object()
    orientation._doc_classifier = object()
    manager.change(False, PLAN)
    manager._thread.join(timeout=5)
    assert manager.status(PLAN)['loaded_count'] == 0
    assert not any(models.loaded_models().values())
    with manager.activity():
        models._load_model('page_v6')
    assert manager.status(PLAN)['state'] == 'partial'


def test_release_is_rejected_for_an_entire_receipt_and_for_direct_prediction(manager):
    def analyze(*args, **kwargs):
        assert manager.status(PLAN)['busy']
        with pytest.raises(models.ModelsBusy): manager.change(False, PLAN)
        return {'ok':True}
    assert RecognitionService(SimpleNamespace(analyze=analyze)).recognize('one.png') == {'ok':True}
    assert not manager.status(PLAN)['busy']
    with paddle._prediction_slot():
        with pytest.raises(models.ModelsBusy): manager.change(False, PLAN)
    assert not manager.status(PLAN)['busy']


def test_loading_blocks_new_recognition_and_duplicate_operations(manager, monkeypatch):
    entered, release, recognized = Event(), Event(), Event()
    original = models._load_model
    def load(key):
        entered.set()
        assert release.wait(timeout=5)
        original(key)
    monkeypatch.setattr(models, '_load_model', load)
    manager.change(True, {'fields':['paddle_v6']})
    assert entered.wait(timeout=2)
    with pytest.raises(models.ModelsBusy): manager.change(False, PLAN)
    def recognize():
        with manager.activity(): recognized.set()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(recognize)
            assert not recognized.wait(timeout=.05)
            release.set()
            pending.result(timeout=5)
    finally:
        release.set()
    assert recognized.is_set()


def test_failure_retains_actual_partial_state_and_retry_recovers(manager, monkeypatch):
    original = models._load_model
    def load(key):
        if key == 'line_v6': raise RuntimeError('secret /Users/private')
        original(key)
    monkeypatch.setattr(models, '_load_model', load)
    manager.change(True, PLAN)
    manager._thread.join(timeout=5)
    state = manager.status(PLAN)
    assert state['state'] == 'partial' and state['loaded_count'] == 1
    assert 'RuntimeError' in state['error'] and 'secret' not in state['error']
    monkeypatch.setattr(models, '_load_model', original)
    manager.change(True, PLAN)
    manager._thread.join(timeout=5)
    assert manager.status(PLAN)['ready'] and not manager.status(PLAN)['error']


def test_receipt_timer_starts_after_manual_initialization_finishes(manager, monkeypatch, tmp_path):
    from receipt_ocr.application import document_service
    from receipt_ocr.runtime import calls
    clock, loading, release, recognized = [0.0], Event(), Event(), Event()
    monkeypatch.setattr(document_service, 'local_model_manager', manager)
    monkeypatch.setattr(calls, 'perf_counter', lambda: clock[0])
    original = models._load_model
    def load(key):
        loading.set()
        assert release.wait(timeout=5)
        clock[0] = 6
        original(key)
    monkeypatch.setattr(models, '_load_model', load)
    source = tmp_path/'receipt.png'
    Image.new('RGB',(10,10),'white').save(source)
    def analyze(*args, **kwargs):
        recognized.set()
        with calls.recognition_call('paddle_v6','read',source): clock[0] += 2
        return {}
    service = document_service.DocumentRecognitionService(analyze,tmp_path/'previews',tmp_path/'artifacts',lambda:'now')
    manager.change(True,{'fields':['paddle_v6']})
    assert loading.wait(timeout=2)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.recognize,source,filename='receipt.png')
            assert not recognized.wait(timeout=.05)
            release.set()
            result, _ = future.result(timeout=5)
    finally:
        release.set()
    assert result['_recognition_calls']['processing_seconds'] == 2
    assert result['_recognition_calls']['model_initialization_seconds'] == 0


def test_model_api_uses_saved_plan_and_never_changes_it(web_queue, manager, monkeypatch):
    from receipt_ocr.web import local_models as handlers
    monkeypatch.setattr(handlers, 'local_model_manager', manager)
    saved = web_queue.database.get_json_setting('recognition_defaults')
    client = web_queue.app.test_client()
    data = client.get('/api/settings/local-models').json
    assert data['state'] in {'unloaded','unneeded'}
    assert manager.seen == []
    response = client.post('/api/settings/local-models', json={'enabled':True})
    assert response.status_code == 202
    manager._thread.join(timeout=5)
    assert client.get('/api/settings/local-models').json['ready']
    with manager.activity():
        assert client.post('/api/settings/local-models', json={'enabled':False}).status_code == 409
    for payload in ({}, {'enabled':1}, {'enabled':'true'}, {'enabled':False,'extra':1}):
        assert client.post('/api/settings/local-models', json=payload).status_code == 400
    assert client.post('/api/settings/local-models',json={'enabled':False},
                       headers={'Origin':'https://other.example'}).status_code == 403
    assert web_queue.database.get_json_setting('recognition_defaults') == saved
    assert client.post('/api/settings/local-models',json={'enabled':False}).status_code == 202
    manager._thread.join(timeout=5)
    assert not client.get('/api/settings/local-models').json['loaded']
