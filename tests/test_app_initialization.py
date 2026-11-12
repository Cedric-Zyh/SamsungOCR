from pathlib import Path
import subprocess
import sys


def test_import_does_not_initialize_or_recover_live_database():
    root = Path(__file__).resolve().parents[1]
    script = '''
from receipt_ocr.persistence.database import Database
def forbidden(*args, **kwargs):
    raise AssertionError('import attempted database access')
Database.connect = forbidden
Database.initialize = forbidden
from receipt_ocr.web import application as app
assert app.application_runtime.initialized is False
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_first_request_initializes_once(monkeypatch, tmp_path):
    from receipt_ocr.web import application as web
    from receipt_ocr.web.runtime import ApplicationRuntime
    from receipt_ocr.runtime.paths import build_runtime_paths
    runtime = ApplicationRuntime(build_runtime_paths(tmp_path))
    monkeypatch.setattr(web, 'application_runtime', runtime)
    calls = []
    def initialize():
        calls.append(True)
        runtime.initialized = True
    monkeypatch.setattr(web, 'initialize', initialize)
    monkeypatch.setattr(runtime, 'maybe_purge', lambda: None)
    with web.app.test_request_context('/'):
        web.ensure_initialized()
        web.ensure_initialized()
    assert calls == [True]
