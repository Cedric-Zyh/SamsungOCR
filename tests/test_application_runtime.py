"""Production resources have an explicit, isolated, idempotent lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from receipt_ocr.runtime.paths import build_runtime_paths
from receipt_ocr.web.runtime import ApplicationRuntime


def make_runtime(tmp_path, monkeypatch, *, fail_start=False):
    calls = []
    workers = []
    class Worker:
        def __init__(self, store, service, **kwargs):
            self.store, self.service = store, service
            workers.append(self)
        def start(self):
            calls.append('start')
            if fail_start and len(workers) == 1:
                raise RuntimeError('start failed')
        def stop(self):
            calls.append('stop')
        def is_alive(self):
            return False
    runtime = ApplicationRuntime(build_runtime_paths(tmp_path, frozen=False, environ={}),
        analyzer=SimpleNamespace(seal_api=SimpleNamespace(api_key='')),
        matcher=SimpleNamespace(refresh=lambda *args: calls.append('refresh')),
        worker_factory=Worker)
    initialize = runtime.database.initialize
    def initialize_database():
        calls.append('database')
        initialize()
    monkeypatch.setattr(runtime.database, 'initialize', initialize_database)
    return runtime, calls, workers


def test_concurrent_start_and_repeated_close_own_exactly_one_worker(tmp_path, monkeypatch):
    runtime, calls, workers = make_runtime(tmp_path, monkeypatch)
    assert not runtime.paths.storage_dir.exists()
    runtime.initialize(start_worker=False)
    assert calls == ['database', 'refresh']
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: runtime.initialize(), range(3)))
    assert calls == ['database', 'refresh', 'start']
    assert workers[0].store is runtime.job_store
    assert workers[0].service.documents.analyze.__self__ is runtime.configured_recognition
    runtime.close()
    runtime.close()
    assert calls.count('stop') == 1
    with pytest.raises(RuntimeError, match='已关闭'):
        runtime.initialize()


def test_partial_worker_start_is_cleaned_up_and_can_retry(tmp_path, monkeypatch):
    runtime, calls, workers = make_runtime(tmp_path, monkeypatch, fail_start=True)
    with pytest.raises(RuntimeError, match='start failed'):
        runtime.initialize()
    assert runtime.job_worker is None
    assert calls == ['database', 'refresh', 'start', 'stop']
    runtime.initialize()
    assert runtime.job_worker is workers[1]
    assert calls.count('database') == calls.count('refresh') == 1
    runtime.close()


def test_failed_preparation_never_starts_worker_and_can_retry(tmp_path, monkeypatch):
    runtime, calls, workers = make_runtime(tmp_path, monkeypatch)
    def fail(*args):
        raise RuntimeError('prepare failed')
    monkeypatch.setattr(runtime.matcher, 'refresh', fail)
    with pytest.raises(RuntimeError, match='prepare failed'):
        runtime.initialize()
    assert not runtime.initialized
    assert workers == []
    monkeypatch.setattr(runtime.matcher, 'refresh', lambda *args: calls.append('refresh'))
    runtime.initialize()
    assert runtime.initialized and len(workers) == 1
    runtime.close()


def test_runtime_instances_and_periodic_maintenance_are_isolated(tmp_path, monkeypatch):
    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    first.initialize(start_worker=False)
    second.initialize(start_worker=False)
    first.database.set_json_setting('qingtong_credentials', {'api_key': 'first-only'})
    first.initialize(start_worker=False)
    assert first.analyzer.seal_api.api_key == 'first-only'
    assert second.analyzer.seal_api.api_key == ''
    calls = []
    monkeypatch.setattr(first.maintenance, 'purge_expired', lambda: calls.append('purge'))
    first._last_cleanup = -10000
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: first.maybe_purge(), range(3)))
    assert calls == ['purge']
    first.close()
    first._last_cleanup = -10000
    first.maybe_purge()
    assert calls == ['purge'] and not second.closed
    second.close()


@pytest.mark.parametrize('partial_start', [False, True])
def test_live_worker_reference_is_retained_until_stop_completes(tmp_path, monkeypatch, partial_start):
    runtime, calls, workers = make_runtime(tmp_path, monkeypatch)
    runtime.initialize(start_worker=False)
    alive = [True]
    class SlowWorker:
        def __init__(self, *args, **kwargs):
            workers.append(self)
        def start(self):
            if partial_start:
                raise RuntimeError('partially started')
        def stop(self):
            calls.append('stop requested')
        def is_alive(self):
            return alive[0]
    runtime._worker_factory = SlowWorker
    if partial_start:
        with pytest.raises(RuntimeError, match='partially started'):
            runtime.initialize()
    else:
        runtime.initialize()
    assert runtime.close() is False
    assert runtime.stopping and not runtime.closed
    assert runtime.job_worker is workers[0]
    with pytest.raises(RuntimeError, match='正在停止'):
        runtime.initialize()
    alive[0] = False
    assert runtime.close() is True
    assert len(workers) == 1 and runtime.closed


@pytest.mark.parametrize('closing', [True, False])
def test_http_does_not_accept_work_after_shutdown(monkeypatch, closing):
    from receipt_ocr.web import application as web
    monkeypatch.setattr(web, 'application_runtime', SimpleNamespace(closed=not closing, stopping=closing))
    response = web.app.test_client().post('/api/tasks', json={})
    assert response.status_code == 503


@pytest.mark.parametrize('partial_start', [False, True])
def test_real_worker_keeps_runtime_ownership_until_inflight_result_finishes(tmp_path, monkeypatch, partial_start):
    from receipt_ocr.jobs.worker import JobWorker
    from receipt_ocr.web import application as web

    runtime, _, _ = make_runtime(tmp_path, monkeypatch)
    runtime.initialize(start_worker=False)
    entered, release = Event(), Event()
    completions = []
    store = SimpleNamespace(
        database=runtime.database, recover=lambda: None, claim=lambda: {'id': 'inflight'},
        update_progress=lambda *args: None, is_paused=lambda: False,
        finish=lambda *args: completions.append((runtime.stopping, runtime.closed)),
    )

    def recognize(job):
        entered.set()
        if not release.wait(10):
            raise RuntimeError('test did not release recognition')
        return {}, ''

    worker = JobWorker(store, recognize)
    original_start, original_stop = worker.start, worker.stop
    monkeypatch.setattr(worker, 'stop', lambda: original_stop(timeout=.01))
    monkeypatch.setattr(runtime, '_worker_factory', lambda *args, **kwargs: worker)
    monkeypatch.setattr(web, 'application_runtime', runtime)

    def start():
        original_start()
        assert entered.wait(2)
        if partial_start:
            raise RuntimeError('partially started')

    monkeypatch.setattr(worker, 'start', start)
    try:
        if partial_start:
            with pytest.raises(RuntimeError, match='partially started'):
                runtime.initialize()
        else:
            runtime.initialize()
        assert runtime.close() is False
        assert worker.is_alive() and runtime.stopping and not runtime.closed
        assert runtime.job_worker is worker
        with pytest.raises(RuntimeError, match='正在停止'):
            runtime.initialize()
        with pytest.raises(RuntimeError, match='不能替换'):
            web.initialize(start_worker=False)
        assert web.application_runtime is runtime
        release.set()
        original_stop(timeout=2)
        assert not worker.is_alive()
        assert completions == [(True, False)]
        assert runtime.close() is True
        assert runtime.closed
    finally:
        release.set()
        original_stop(timeout=2)
        runtime.close()


@pytest.mark.parametrize('failed_thread', ['local', 'remote', 'images'])
def test_thread_start_failure_cleans_up_and_allows_retry(tmp_path, monkeypatch, failed_thread):
    from receipt_ocr.jobs.worker import JobWorker

    runtime, calls, _ = make_runtime(tmp_path, monkeypatch)
    runtime.initialize(start_worker=False)
    original_factory = runtime._worker_factory
    worker = JobWorker(runtime.job_store, SimpleNamespace(source_for=lambda job: None))
    if failed_thread == 'local':
        worker.remote = None
    elif failed_thread == 'remote':
        # Reach the remote thread's start without starting an image thread.
        monkeypatch.setattr(worker.remote.images, 'start', lambda: None)
    monkeypatch.setattr(runtime, '_worker_factory', lambda *args, **kwargs: worker)
    attempted_threads = []

    def fail_start(thread):
        attempted_threads.append(thread.name)
        raise RuntimeError('cannot start a new thread')

    with monkeypatch.context() as starting:
        starting.setattr(Thread, 'start', fail_start)
        with pytest.raises(RuntimeError, match='cannot start a new thread'):
            runtime.initialize()
    assert attempted_threads == [{
        'local': 'receipt-queue', 'remote': 'danzhengtong-queue', 'images': 'danzhengtong-images',
    }[failed_thread]]
    assert not worker.is_alive()
    assert runtime.job_worker is None and not runtime.stopping
    monkeypatch.setattr(runtime, '_worker_factory', original_factory)
    runtime.initialize()
    assert calls.count('database') == calls.count('refresh') == 1
    assert runtime.close() is True


@pytest.mark.parametrize('active_thread', ['local', 'remote', 'images'])
def test_worker_liveness_includes_every_owned_thread(active_thread):
    from receipt_ocr.jobs.worker import JobWorker
    worker = JobWorker(SimpleNamespace(), SimpleNamespace(source_for=lambda job: None))
    assert not worker.is_alive()
    live = SimpleNamespace(is_alive=lambda: True)
    if active_thread == 'local':
        worker._thread = live
    elif active_thread == 'remote':
        worker.remote._thread = live
    else:
        worker.remote.images._thread = live
    assert worker.is_alive()
