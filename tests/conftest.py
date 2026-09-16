"""Web tests never initialize or write the user's live receipt storage."""
import pytest


@pytest.fixture(scope='session', autouse=True)
def isolated_application(tmp_path_factory):
    import app as web
    from receipt_ocr.database import Database
    from receipt_ocr.seal_reference import SealReferenceMatcher

    root = tmp_path_factory.mktemp('application')
    patch = pytest.MonkeyPatch()
    patch.setitem(web.app.config, 'BACKGROUND_WORKER', False)
    patch.setattr(web, 'job_store', web.job_store)
    for name, child in (
        ('STORAGE_DIR', ''), ('UPLOAD_DIR', 'uploads'),
        ('PREVIEW_DIR', 'previews'), ('ARTIFACT_DIR', 'artifacts'),
        ('EXPORT_DIR', 'exports'),
    ):
        patch.setattr(web, name, root / child)
    patch.setattr(web, 'DATABASE_PATH', root / 'results.db')
    patch.setattr(web, 'GROUND_TRUTH_PATH', root / 'ground_truth.json')
    patch.setattr(web, 'database', Database(root / 'results.db'))
    patch.setattr(web, 'seal_reference_matcher', SealReferenceMatcher(root / 'artifacts'))
    patch.setattr(web, '_initialized', False)
    web.initialize()
    yield
    patch.undo()
