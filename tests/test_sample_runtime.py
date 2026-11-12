"""Resource ownership and HTTP independence for the sample service."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace

import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.samples.runtime import SampleRuntime
from receipt_ocr.samples.service import SampleCenter, SampleNotFound, SampleUnavailable
from sample_support import sample_dependencies
from test_sample_center import samples


def test_sample_service_runs_without_flask_application_or_request_context(samples):
    ctx, _ = samples
    service = SampleCenter(sample_dependencies(ctx))
    assert service.listing()['total'] == 4
    assert service.source('one.png') == ctx.DATA_DIR / 'one.png'
    with pytest.raises(SampleNotFound):
        service.source('missing.png')
    with pytest.raises(SampleNotFound):
        service.result(999999)
    assert service.history() == []
    service.close()


def test_lazy_runtime_has_one_owner_and_closes_before_switching_storage(samples, tmp_path, monkeypatch):
    ctx, _ = samples
    current = [sample_dependencies(ctx)]
    runtime = SampleRuntime(lambda: current[0])
    sample_database = ctx.database.path.parent / 'sample-center' / 'results.db'
    assert not sample_database.exists()
    with ThreadPoolExecutor(max_workers=4) as executor:
        centers = list(executor.map(lambda _: runtime.get(), range(8)))
    first = centers[0]
    assert all(center is first for center in centers)
    closed = []
    monkeypatch.setattr(first, 'close', lambda: closed.append('first') or True)
    other = Database(tmp_path / 'other' / 'results.db')
    other.initialize()
    current[0] = replace(current[0], database=other)
    second = runtime.get()
    assert second is not first
    assert second.database.path.parent.parent == other.path.parent
    assert closed == ['first']
    monkeypatch.setattr(second, 'close', lambda: closed.append('second') or True)
    runtime.close()
    runtime.close()
    assert closed == ['first', 'second']


def test_route_only_maps_missing_sample_and_does_not_hide_programming_errors(samples, monkeypatch):
    ctx, client = samples
    assert client.get('/api/samples/results/999999').status_code == 404
    center = ctx.app.extensions['sample_runtime'].get()

    def broken_listing(*args):
        raise KeyError('unexpected internal field')

    monkeypatch.setattr(center, 'listing', broken_listing)
    with pytest.raises(KeyError, match='unexpected internal field'):
        client.get('/api/samples')


def test_runtime_switches_dataset_without_reusing_old_catalog_or_sources(samples, tmp_path, monkeypatch):
    ctx, _ = samples
    current = [sample_dependencies(ctx)]
    runtime = SampleRuntime(lambda: current[0])
    first = runtime.get()
    assert first.listing()['total'] == 4
    closed = []
    monkeypatch.setattr(first, 'close', lambda: closed.append(True) or True)
    data = tmp_path / 'new-dataset'
    data.mkdir()
    (data / 'one.png').write_bytes((ctx.DATA_DIR / 'one.png').read_bytes())
    truth = tmp_path / 'new-truth.json'
    truth.write_text(json.dumps({'one.png': {'fields': {'客户名称': '新数据集'}}}))
    paths = replace(current[0].paths, data_dir=data, ground_truth_path=truth)
    current[0] = replace(current[0], paths=paths)
    second = runtime.get()
    assert closed == [True]
    assert second.database.path == first.database.path
    assert second.source('one.png') == data / 'one.png'
    assert second.listing()['companies'] == [{'name': '新数据集', 'count': 1}]
    assert second.listing()['total'] == 1
    runtime.close()


def test_sample_stop_timeout_retains_worker_and_blocks_restart_or_owner_replacement(samples, monkeypatch):
    from receipt_ocr.jobs.worker import JobWorker

    ctx, _ = samples
    first_owner, next_owner = object(), object()
    current = [replace(sample_dependencies(ctx), owner_token=first_owner)]
    runtime = SampleRuntime(lambda: current[0])
    assert runtime.bind_owner(first_owner)
    center = runtime.get()
    entered, release = Event(), Event()
    completions = []
    store = SimpleNamespace(database=center.database, recover=lambda: None,
        claim=lambda: {'id': 'inflight'}, update_progress=lambda *args: None,
        is_paused=lambda: False,
        finish=lambda *args: completions.append((center.stopping, center.closed)))
    def execute(job):
        entered.set()
        assert release.wait(5)
        return {}, ''
    worker = JobWorker(store, execute)
    original_stop = worker.stop
    monkeypatch.setattr(worker, 'stop', lambda: original_stop(timeout=.01))
    monkeypatch.setattr(center, 'worker', worker)
    try:
        center.start_worker()
        assert entered.wait(2)
        assert runtime.close() is False
        assert runtime._center is center and center.worker is worker
        assert center.stopping and not center.closed and worker.is_alive()
        with pytest.raises(SampleUnavailable):
            center.start_worker()
        with pytest.raises(SampleUnavailable):
            runtime.get()
        current[0] = replace(current[0], owner_token=next_owner)
        assert runtime.bind_owner(next_owner) is False
        assert runtime._owner_token is first_owner and runtime._center is center
        release.set()
        original_stop(timeout=2)
        assert not worker.is_alive()
        assert completions == [(True, False)]
        assert runtime.bind_owner(next_owner) is True
        assert center.closed
        assert runtime.get() is not center
        assert runtime.get().dependencies.owner_token is next_owner
        # An accepted request carrying the retired token cannot recreate it.
        current[0] = replace(current[0], owner_token=first_owner)
        with pytest.raises(SampleUnavailable, match='所属'):
            runtime.get()
    finally:
        release.set()
        original_stop(timeout=2)
        runtime.close()


def test_implicit_sample_owner_switch_waits_for_the_old_worker(samples, monkeypatch):
    ctx, _ = samples
    current = [sample_dependencies(ctx)]
    runtime = SampleRuntime(lambda: current[0])
    center = runtime.get()
    alive = [True]
    monkeypatch.setattr(center.worker, 'stop', lambda: None)
    monkeypatch.setattr(center.worker, 'is_alive', lambda: alive[0])
    current[0] = replace(current[0], owner_token=object())
    with pytest.raises(SampleUnavailable, match='不能替换'):
        runtime.get()
    assert runtime._center is center and not center.closed
    alive[0] = False
    assert runtime.get() is not center
    assert center.closed
    assert runtime.close()


def test_sample_partial_start_cleanup_keeps_a_still_running_worker(samples, monkeypatch):
    ctx, _ = samples
    center = SampleCenter(sample_dependencies(ctx))
    alive = [True]
    calls = []
    def fail_start():
        raise RuntimeError('partially started')
    monkeypatch.setattr(center.worker, 'start', fail_start)
    monkeypatch.setattr(center.worker, 'stop', lambda: calls.append('stop'))
    monkeypatch.setattr(center.worker, 'is_alive', lambda: alive[0])
    with pytest.raises(RuntimeError, match='partially started'):
        center.start_worker()
    assert calls == ['stop'] and center.stopping and not center.closed
    with pytest.raises(SampleUnavailable):
        center.start_worker()
    assert center.close() is False
    alive[0] = False
    assert center.close() is True
