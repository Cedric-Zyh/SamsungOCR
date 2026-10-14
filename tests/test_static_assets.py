"""Exercise browser-facing assets with the bad MIME mapping seen on Windows."""
import mimetypes

import pytest

from receipt_ocr.web import application as web


@pytest.fixture
def client(monkeypatch):
    # Simulate OS/registry mappings after Python has initialized its MIME table.
    mimetypes.init()
    for suffix in ('.js', '.mjs'):
        monkeypatch.setitem(mimetypes.types_map, suffix, 'text/plain')
    return web.app.test_client()


def test_all_scripts_have_javascript_content_type(client):
    scripts = sorted(path for path in (web.RESOURCE_DIR / 'static').rglob('*')
                     if path.suffix in {'.js', '.mjs'})
    assert scripts
    for path in scripts:
        relative = path.relative_to(web.RESOURCE_DIR / 'static').as_posix()
        with client.get(f'/static/{relative}?v=regression') as response:
            assert response.status_code == 200, relative
            assert response.mimetype == 'text/javascript', relative
            assert response.data == path.read_bytes(), relative


def test_script_cache_and_range_responses(client):
    with client.get('/static/app.js') as response:
        etag = response.headers['ETag']
        original = response.data
    with client.get('/static/app.js', headers={'If-None-Match': etag}) as response:
        assert response.status_code == 304
        assert not response.data
    with client.get('/static/app.js', headers={'Range': 'bytes=0-9'}) as response:
        assert response.status_code == 206
        assert response.mimetype == 'text/javascript'
        assert response.data == original[:10]


@pytest.mark.parametrize(('url', 'status', 'mime'), [
    ('/static/missing-module.mjs', 404, 'text/html'),
    ('/static/ui.css', 200, 'text/css'),
    ('/', 200, 'text/html'),
])
def test_other_responses_keep_their_content_type(client, url, status, mime):
    with client.get(url) as response:
        assert response.status_code == status
        assert response.mimetype == mime
