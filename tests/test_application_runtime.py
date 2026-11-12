"""Production resources have an explicit, isolated, idempotent lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from receipt_ocr.runtime.paths import build_runtime_paths
from receipt_ocr.web.runtime import ApplicationRuntime


@pytest.fixture(autouse=True)
def isolated_sample_owner(monkeypatch):
    from receipt_ocr.web import application as web
    from receipt_ocr.samples.runtime import SampleRuntime

    runtime = SampleRuntime(web._sample_dependencies)
    monkeypatch.setitem(web.app.extensions, 'sample_runtime', runtime)
    yield runtime
    runtime.close()


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
        analyzer=SimpleNamespace(seal_api=SimpleNamespace(api_key='', enabled=False)),
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


def test_legacy_reads_follow_the_owner_without_storing_resource_aliases(tmp_path, monkeypatch):
    import app as entrypoint
    from receipt_ocr.web import application as web

    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    monkeypatch.setattr(web, 'application_runtime', first)
    for module in (web, entrypoint):
        assert 'database' not in vars(module)
        assert 'job_store' not in vars(module)
        assert module.database is first.database
        assert module.GROUND_TRUTH_PATH == first.paths.ground_truth_path
    web.install_runtime(second)
    assert first.closed and not second.initialized
    for module in (web, entrypoint):
        assert module.database is second.database
        assert module.job_store is second.job_store
        assert module.analyzer is second.analyzer
        assert module.seal_reference_matcher is second.matcher
        assert module.recognition_service is second.recognition
        assert module.RUNTIME_PATHS is second.paths
        assert module._initialized is False
        assert module.DATABASE_PATH == second.database.path
    assert entrypoint.application_runtime is second
    assert not first.paths.storage_dir.exists()
    assert not second.paths.storage_dir.exists()
    with pytest.raises(AttributeError):
        getattr(web, 'missing_legacy_resource')


def test_http_factories_and_worker_share_the_installed_owner(tmp_path, monkeypatch):
    from receipt_ocr.web import application as web

    runtime, calls, workers = make_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(web, 'application_runtime', runtime)
    monkeypatch.setitem(web.app.config, 'BACKGROUND_WORKER', True)
    observed = []
    def recognize(*args, **kwargs):
        observed.append((args, kwargs))
        return {'overall': '需人工复核'}
    monkeypatch.setattr(runtime.configured_recognition, 'recognize', recognize)
    web.initialize()
    web.initialize()
    assert calls == ['database', 'refresh', 'start']
    for factory in (web._queue_dependencies, web._recognition_dependencies,
                    web._results_dependencies, web._review_dependencies,
                    web._sample_dependencies, web._standard_dependencies):
        assert factory().database is runtime.database
    queue = web._queue_dependencies()
    assert queue.job_store is workers[0].store is runtime.job_store
    assert queue.job_worker is runtime.job_worker
    assert web._document_service().analyze is recognize
    assert web._sample_dependencies().analyze is recognize
    assert workers[0].service.documents.analyze is recognize
    assert web._document_service().artifact_dir == runtime.paths.artifact_dir
    assert web._review_dependencies().seal_reference_matcher is runtime.matcher
    assert web._configured_analyze('legacy.png')['overall'] == '需人工复核'
    assert observed[0][0] == ('legacy.png',)
    runtime.database.set_retention_days(23)
    assert web.app.test_client().get('/api/settings').json['retention_days'] == 23
    runtime.close()


def test_install_requires_previous_owner_to_finish_closing(tmp_path, monkeypatch):
    from receipt_ocr.web import application as web

    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    monkeypatch.setattr(web, 'application_runtime', first)
    first.initialize(start_worker=False)
    web.install_runtime(first)
    with pytest.raises(RuntimeError, match='关闭后再替换'):
        web.install_runtime(second)
    assert web.application_runtime is first and not first.closed
    first.close()
    web.install_runtime(second)
    assert web.application_runtime is second
    second.close()
    third, _, _ = make_runtime(tmp_path / 'third', monkeypatch)
    third.close()
    with pytest.raises(RuntimeError, match='已关闭'):
        web.install_runtime(third)
    assert web.application_runtime is second


def test_supplied_queue_cannot_belong_to_a_different_database(tmp_path, monkeypatch):
    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    with pytest.raises(ValueError, match='运行环境的数据库'):
        ApplicationRuntime(first.paths, database=first.database, job_store=second.job_store)
    assert not first.paths.storage_dir.exists()
    assert not second.paths.storage_dir.exists()


def test_explicit_database_normalizes_runtime_paths_without_initializing(tmp_path, monkeypatch):
    from receipt_ocr.persistence.database import Database
    from receipt_ocr.web import application as web

    paths = build_runtime_paths(tmp_path, frozen=False, environ={})
    database = Database(tmp_path / 'injected.db')
    runtime = ApplicationRuntime(paths, database=database)
    monkeypatch.setattr(web, 'application_runtime', runtime)
    assert runtime.database is runtime.job_store.database is database
    assert runtime.paths.database_path == web.DATABASE_PATH == database.path
    assert runtime.paths.artifact_dir == paths.artifact_dir
    assert paths.database_path != database.path  # The caller's immutable paths are not changed.
    assert not database.path.exists()
    assert not paths.storage_dir.exists()


def test_captured_services_keep_their_owner_after_it_is_replaced(tmp_path, monkeypatch):
    from receipt_ocr.web import application as web

    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    for runtime, standard_id in ((first, 11), (second, 22)):
        runtime.initialize(start_worker=False)
        (runtime.paths.upload_dir / 'same.png').write_bytes(b'image')
        runtime.database.insert_result(filename='same.png', stored_name='same.png',
            preview_name='', task_id='', result={
                'fields': {'签章要求': '测试收货章'}, 'overall': '需人工复核'})
        standard = {'id': standard_id, 'revision': 1, 'shape': 'round', 'color': 'red'}
        monkeypatch.setattr(runtime.database, 'get_seal_standard',
            lambda *args, _standard=standard, **kwargs: _standard)
    monkeypatch.setattr(web, 'application_runtime', first)
    retry = web._retry_service()
    review = web._review_dependencies()
    projections = [factory().project_result for factory in
        (web._results_dependencies, web._sample_dependencies, web._standard_dependencies)]
    assert first.close()
    web.install_runtime(second)

    assert retry.database is first.database
    assert retry.documents.analyze.__self__ is first.configured_recognition
    assert retry.prepare(1, {}).source == first.paths.upload_dir / 'same.png'
    current = first.database.get_result(1)
    for project in [*projections, review.project_result]:
        assert project(current)['seal_standard_check']['standard']['id'] == 11
    reviewed = review.apply_edits(current, {'review_status': '待复核'})
    assert reviewed['seal_standard_check']['standard']['id'] == 11
    assert web._retry_service().prepare(1, {}).source == second.paths.upload_dir / 'same.png'
    assert web._results_dependencies().project_result(current)['seal_standard_check']['standard']['id'] == 22
    second.close()


def test_accepted_http_request_keeps_its_owner_until_the_response(tmp_path, monkeypatch):
    from receipt_ocr.web import application as web

    first, _, _ = make_runtime(tmp_path / 'first', monkeypatch)
    second, _, _ = make_runtime(tmp_path / 'second', monkeypatch)
    first.initialize(start_worker=False)
    second.initialize(start_worker=False)
    first.database.set_retention_days(19)
    second.database.set_retention_days(27)
    monkeypatch.setattr(web, 'application_runtime', first)
    with web.app.test_request_context('/api/settings'):
        web.ensure_initialized()
        first.close()
        web.install_runtime(second)
        assert web._queue_dependencies().database is first.database
        assert web._document_service().analyze.__self__ is first.configured_recognition
        assert web.get_settings().json['retention_days'] == 19
    assert web.app.test_client().get('/api/settings').json['retention_days'] == 27
    second.close()


@pytest.mark.parametrize('close_all', [False, True])
def test_same_path_owner_switch_waits_for_samples_and_rebinds_their_recognizer(
        tmp_path, monkeypatch, isolated_sample_owner, close_all):
    from receipt_ocr.web import application as web
    from receipt_ocr.samples.service import SampleUnavailable

    first, _, _ = make_runtime(tmp_path, monkeypatch)
    second, _, _ = make_runtime(tmp_path, monkeypatch)
    first.initialize(start_worker=False)
    monkeypatch.setattr(web, 'application_runtime', first)
    assert isolated_sample_owner.bind_owner(first)
    old_center = isolated_sample_owner.get()
    assert old_center.dependencies.analyze.__self__ is first.configured_recognition
    alive = [True]
    monkeypatch.setattr(old_center.worker, 'stop', lambda: None)
    monkeypatch.setattr(old_center.worker, 'is_alive', lambda: alive[0])
    if close_all:
        assert web.shutdown() is False
        assert web.app.test_client().get('/api/samples').status_code == 503
    else:
        assert first.close()
    with pytest.raises(RuntimeError, match='样单后台任务尚未退出'):
        web.install_runtime(second)
    assert web.application_runtime is first
    assert isolated_sample_owner._center is old_center
    with pytest.raises(SampleUnavailable):
        old_center.start_worker()
    alive[0] = False
    web.install_runtime(second)
    new_center = isolated_sample_owner.get()
    assert new_center is not old_center and old_center.closed
    assert new_center.database.path == old_center.database.path
    assert new_center.dependencies.analyze.__self__ is second.configured_recognition
    assert new_center.dependencies.owner_token is second
    assert web.shutdown() is True


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
        replacement, _, _ = make_runtime(tmp_path / 'replacement', monkeypatch)
        with pytest.raises(RuntimeError, match='不能替换'):
            web.install_runtime(replacement)
        with pytest.raises(RuntimeError, match='正在停止'):
            web.initialize(start_worker=False)
        assert web.application_runtime is runtime
        release.set()
        original_stop(timeout=2)
        assert not worker.is_alive()
        assert completions == [(True, False)]
        assert runtime.close() is True
        assert runtime.closed
        web.install_runtime(replacement)
        assert web.application_runtime is replacement
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
