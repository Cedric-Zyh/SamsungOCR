from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import json
import os
import threading

from PIL import Image
import pytest

from receipt_ocr.web.sample_library import SampleLibrary
from receipt_ocr.web.sample_thumbnails import SampleThumbnails
from receipt_ocr.web.samples import SampleCenter
from test_sample_center import samples


def test_thumbnail_cache_survives_new_instance_and_conditional_requests(samples, monkeypatch):
    ctx, client = samples
    url = '/api/samples/image/one.png?thumbnail=1'
    first = client.get(url)
    assert first.status_code == 200
    assert first.headers['ETag']
    assert 'max-age=0' in first.headers['Cache-Control']

    def unexpected(*args, **kwargs):
        raise AssertionError('a cached thumbnail must not decode or scan the sample catalog')

    monkeypatch.setattr(Image, 'open', unexpected)
    monkeypatch.setattr(SampleLibrary, 'catalog', unexpected)
    cached = client.get(url)
    assert cached.status_code == 200 and cached.data == first.data
    unchanged = client.get(url, headers={'If-None-Match': first.headers['ETag']})
    assert unchanged.status_code == 304 and unchanged.data == b''
    cache = SampleThumbnails(ctx.database.path.parent / 'sample-center' / 'thumbnails')
    data, etag = cache.get(ctx.DATA_DIR / 'one.png')
    assert data == first.data and f'"{etag}"' == first.headers['ETag']


def test_source_changes_and_conversion_version_retire_old_thumbnail(samples, monkeypatch):
    ctx, client = samples
    url = '/api/samples/image/one.png?thumbnail=1'
    first = client.get(url)
    source = ctx.DATA_DIR / 'one.png'
    previous = source.stat()
    Image.new('RGB', (600, 900), 'red').save(source)
    os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
    changed = client.get(url, headers={'If-None-Match': first.headers['ETag']})
    assert changed.status_code == 200
    assert changed.headers['ETag'] != first.headers['ETag']
    with Image.open(BytesIO(changed.data)) as image:
        assert image.size == (320, 480)
        assert image.getpixel((10, 10))[0] > 240
    with Image.open(source) as image:
        assert image.size == (600, 900)
    monkeypatch.setattr(SampleThumbnails, 'VERSION', 'test-conversion-v2')
    versioned = client.get(url, headers={'If-None-Match': changed.headers['ETag']})
    assert versioned.status_code == 200
    assert versioned.headers['ETag'] != changed.headers['ETag']
    cache_dir = ctx.database.path.parent / 'sample-center' / 'thumbnails'
    assert len(list(cache_dir.glob('*.jpg'))) == 1
    assert not list(cache_dir.glob('*.tmp'))


def test_cached_thumbnail_cannot_bypass_deletion_or_builtin_allowlist(samples):
    ctx, client = samples
    url = '/api/samples/image/one.png?thumbnail=1'
    first = client.get(url)
    assert first.status_code == 200
    truth = json.loads(ctx.GROUND_TRUTH_PATH.read_text())
    truth.pop('one.png')
    ctx.GROUND_TRUTH_PATH.write_text(json.dumps(truth), encoding='utf-8')
    assert client.get(url, headers={'If-None-Match': first.headers['ETag']}).status_code == 404

    assert client.get('/api/samples/image/two.png?thumbnail=1').status_code == 200
    assert client.post('/api/samples/delete', json={'filenames': ['two.png']}).status_code == 200
    assert client.get('/api/samples/image/two.png?thumbnail=1').status_code == 404


def test_source_lookup_preserves_deleted_builtin_access_and_rejects_storage_escape(samples, monkeypatch):
    ctx, client = samples
    center = SampleCenter(ctx)
    monkeypatch.setattr(center.library, 'catalog', lambda **kwargs: pytest.fail('source scanned the catalog'))
    assert center.library.source('one.png') == (ctx.DATA_DIR / 'one.png').resolve()
    with center.database.connect() as con:
        con.execute('INSERT INTO sample_library(filename,deleted_at) VALUES(?,?)', ('one.png', 'deleted'))
        con.execute('INSERT INTO sample_library(filename,storage_name) VALUES(?,?)', ('escape.png', '../outside.png'))
    with pytest.raises(KeyError):
        center.library.source('one.png')
    assert center.library.source('one.png', include_deleted=True) == (ctx.DATA_DIR / 'one.png').resolve()
    for name in ('escape.png', '../outside.png', 'symlink.png', 'missing.png'):
        with pytest.raises(KeyError):
            center.library.source(name, include_deleted=True)


def test_concurrent_thumbnail_requests_share_one_decode(tmp_path, monkeypatch):
    source = tmp_path / 'sample.png'
    Image.new('RGB', (600, 900), 'white').save(source)
    cache = SampleThumbnails(tmp_path / 'cache')
    original_open = Image.open
    calls, count_lock = [], threading.Lock()
    barrier = threading.Barrier(8)

    def observed_open(*args, **kwargs):
        with count_lock:
            calls.append(args[0])
        return original_open(*args, **kwargs)

    def request_thumbnail(_):
        barrier.wait()
        return cache.get(source)

    monkeypatch.setattr(Image, 'open', observed_open)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(request_thumbnail, range(8)))
    assert len(calls) == 1
    assert all(response == responses[0] for response in responses)
    assert len(list(cache.directory.iterdir())) == 1
    with original_open(BytesIO(responses[0][0])) as image:
        image.verify()


def test_independent_cache_instances_publish_complete_jpegs(tmp_path, monkeypatch):
    source = tmp_path / 'sample.jpg'
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (600, 900), 'white').save(source, exif=exif)
    caches = [SampleThumbnails(tmp_path / 'cache') for _ in range(4)]
    barrier = threading.Barrier(len(caches))
    original_open = Image.open
    calls = []

    def observed_open(*args, **kwargs):
        calls.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, 'open', observed_open)

    def request_thumbnail(cache):
        barrier.wait()
        return cache.get(source)

    with ThreadPoolExecutor(max_workers=len(caches)) as pool:
        responses = list(pool.map(request_thumbnail, caches))
    assert all(response == responses[0] for response in responses)
    assert len(calls) == 1
    assert len(list(caches[0].directory.iterdir())) == 1
    with Image.open(BytesIO(responses[0][0])) as image:
        assert image.size == (480, 320)
        assert image.getexif().get(274) is None


@pytest.mark.parametrize('winner', [False, True])
def test_replace_permission_failure_only_reuses_an_already_published_thumbnail(tmp_path, monkeypatch, winner):
    source = tmp_path / 'sample.png'
    Image.new('RGB', (600, 900), 'red').save(source)
    cache = SampleThumbnails(tmp_path / 'cache')

    def competing_replace(temporary, target):
        if winner:
            Path(target).write_bytes(Path(temporary).read_bytes())
        raise PermissionError('another process has the destination open')

    monkeypatch.setattr(os, 'replace', competing_replace)
    if winner:
        data, key = cache.get(source)
        with Image.open(BytesIO(data)) as image:
            image.verify()
        assert len(list(cache.directory.glob('*.jpg'))) == 1 and key
    else:
        with pytest.raises(PermissionError):
            cache.get(source)
        assert not list(cache.directory.glob('*.jpg'))
    assert not list(cache.directory.glob('*.tmp'))
