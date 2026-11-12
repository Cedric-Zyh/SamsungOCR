"""Web tests never initialize or write the user's live receipt storage."""
from dataclasses import replace
import time

import pytest


@pytest.fixture(scope='session', autouse=True)
def isolated_application(tmp_path_factory):
    from receipt_ocr.web import application as web
    from receipt_ocr.web.runtime import ApplicationRuntime
    from receipt_ocr.samples.runtime import SampleRuntime

    root = tmp_path_factory.mktemp('application')
    patch = pytest.MonkeyPatch()
    patch.setitem(web.app.config, 'BACKGROUND_WORKER', False)
    paths = replace(web.application_runtime.paths, storage_dir=root,
        upload_dir=root / 'uploads', preview_dir=root / 'previews',
        artifact_dir=root / 'artifacts', export_dir=root / 'exports',
        database_path=root / 'results.db', ground_truth_path=root / 'ground_truth.json')
    runtime = ApplicationRuntime(paths, logger=web.app.logger)
    patch.setattr(web, 'application_runtime', runtime)
    samples = SampleRuntime(web._sample_dependencies)
    samples.bind_owner(runtime)
    patch.setitem(web.app.extensions, 'sample_runtime', samples)
    web.initialize()
    yield runtime
    runtime.close()
    samples.close()
    patch.undo()


@pytest.fixture
def bind_application_runtime(monkeypatch):
    """Inject one coherent owner for HTTP tests with already prepared databases.

    These tests prepare their own rows and queues; running recovery or retention
    would change those fixtures. Lifecycle tests exercise initialize separately.
    """
    from receipt_ocr.web import application as web
    from receipt_ocr.web.runtime import ApplicationRuntime
    from receipt_ocr.samples.runtime import SampleRuntime

    created = []

    def bind(*, database=None, job_store=None, analyzer=None, matcher=None,
             recognition=None, **path_overrides):
        previous = web.application_runtime
        if database is None:
            database = job_store.database if job_store is not None else previous.database
        paths = replace(previous.paths, database_path=database.path, **path_overrides)
        runtime = ApplicationRuntime(paths, database=database, job_store=job_store,
            analyzer=previous.analyzer if analyzer is None else analyzer,
            matcher=matcher, recognition=recognition, logger=web.app.logger)
        runtime.job_store.initialize()
        runtime.initialized = True
        runtime._last_cleanup = time.monotonic()
        monkeypatch.setattr(web, 'application_runtime', runtime)
        samples = SampleRuntime(web._sample_dependencies)
        samples.bind_owner(runtime)
        monkeypatch.setitem(web.app.extensions, 'sample_runtime', samples)
        created.append((runtime, samples))
        return runtime

    yield bind
    for runtime, samples in reversed(created):
        runtime.close()
        samples.close()
