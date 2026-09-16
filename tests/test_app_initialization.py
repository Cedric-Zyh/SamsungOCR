from pathlib import Path
import subprocess
import sys


def test_import_does_not_initialize_or_recover_live_database():
    root = Path(__file__).resolve().parents[1]
    script = '''
from receipt_ocr.database import Database
def forbidden(*args, **kwargs):
    raise AssertionError('import attempted database access')
Database.connect = forbidden
Database.initialize = forbidden
import app
assert app._initialized is False
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_first_request_initializes_once(monkeypatch):
    import app as web
    calls = []
    def initialize():
        calls.append(True)
        web._initialized = True
    monkeypatch.setattr(web, '_initialized', False)
    monkeypatch.setattr(web, 'initialize', initialize)
    with web.app.test_request_context('/'):
        web.ensure_initialized()
        web.ensure_initialized()
    assert calls == [True]
