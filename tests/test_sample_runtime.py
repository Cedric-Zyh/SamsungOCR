"""Resource ownership and HTTP independence for the sample service."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.samples.runtime import SampleRuntime
from receipt_ocr.samples.service import SampleCenter, SampleNotFound
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
    monkeypatch.setattr(first, 'close', lambda: closed.append('first'))
    other = Database(tmp_path / 'other' / 'results.db')
    other.initialize()
    current[0] = replace(current[0], database=other)
    second = runtime.get()
    assert second is not first
    assert second.database.path.parent.parent == other.path.parent
    assert closed == ['first']
    monkeypatch.setattr(second, 'close', lambda: closed.append('second'))
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
    monkeypatch.setattr(first, 'close', lambda: closed.append(True))
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
