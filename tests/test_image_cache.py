from pathlib import Path
import os

import numpy as np
import pytest

from receipt_ocr.imaging import io
from receipt_ocr.imaging.working_images import image_workspace_scope


def test_image_cache_decodes_each_path_once_and_returns_isolated_arrays(
    tmp_path, monkeypatch
):
    source = tmp_path / "receipt.jpg"
    source.write_bytes(b"placeholder")
    calls = []

    def decode(path):
        calls.append(Path(path).resolve())
        return np.zeros((4, 5, 3), dtype=np.uint8)

    monkeypatch.setattr(io, "_decode_image", decode)
    cache = io.ImageCache()
    with io.image_cache_scope(cache):
        first = io._read_image(source)
        first[0, 0, 0] = 255
        second = io._read_image(source)

    assert calls == [source.resolve()]
    assert second[0, 0, 0] == 0
    assert cache.images == {}


def test_cache_invalidates_overwritten_files_and_bounds_memory(tmp_path, monkeypatch):
    source = tmp_path / "crop.png"
    other = tmp_path / "other.png"
    source.write_bytes(b"a")
    other.write_bytes(b"b")
    calls = []
    def decode(path):
        calls.append(path.read_bytes())
        return np.full((2, 2), len(path.read_bytes()), dtype=np.uint8)
    monkeypatch.setattr(io, "_decode_image", decode)
    cache = io.ImageCache(max_bytes=4)
    with io.image_cache_scope(cache):
        assert io._read_image(source)[0, 0] == 1
        source.write_bytes(b"changed")
        assert io._read_image(source)[0, 0] == 7
        io._read_image(other)
        assert len(cache.images) == 1
        io._read_image(source)
    assert calls == [b"a", b"changed", b"b", b"changed"]


def test_nested_scope_keeps_outer_cache_and_error_releases_it(tmp_path, monkeypatch):
    source = tmp_path / "image.png"
    source.touch()
    calls = []
    monkeypatch.setattr(io, "_decode_image", lambda p: calls.append(p) or np.zeros((2, 2)))
    cache = io.ImageCache()
    with pytest.raises(RuntimeError):
        with io.image_cache_scope(cache):
            io._read_image(source)
            with io.image_cache_scope(cache):
                io._read_image(source)
            io._read_image(source)
            raise RuntimeError("stage failed")
    assert len(calls) == 1
    assert not cache.images
    assert io._active_cache.get() is None


def test_source_and_working_images_share_one_pixel_store_without_mutation(tmp_path, monkeypatch):
    source = tmp_path / 'source.png'
    source.write_bytes(b'image')
    frame = np.full((4, 5, 3), 73, dtype=np.uint8)
    reads = []
    monkeypatch.setattr(io, '_decode_pixels', lambda path: reads.append(path) or frame.copy())
    cache = io.ImageCache()

    with io.image_cache_scope(cache), image_workspace_scope() as workspace:
        first = io._read_image(source)
        first[:] = 0
        second = workspace.cv_read(source)
        second[:] = 255
        np.testing.assert_array_equal(io._read_image(source), frame)
        assert len(reads) == 1
        assert not cache.images and cache._bytes == 0
        assert workspace.bytes == frame.nbytes
        assert len(workspace.frames) == 1
    assert workspace.bytes == 0 and not workspace.frames


def test_shared_store_invalidates_rewrites_and_uses_one_memory_budget(tmp_path, monkeypatch):
    source, other = tmp_path / 'source.png', tmp_path / 'other.png'
    source.write_bytes(b'a')
    other.write_bytes(b'bb')
    reads = []

    def decode(path):
        data = Path(path).read_bytes()
        reads.append(data)
        return np.full((2, 2), len(data), dtype=np.uint8)

    monkeypatch.setattr(io, '_decode_pixels', decode)
    cache = io.ImageCache()
    with io.image_cache_scope(cache), image_workspace_scope(max_bytes=4) as workspace:
        assert io._read_image(source)[0, 0] == 1
        version = source.stat()
        source.write_bytes(b'changed')
        os.utime(source, ns=(version.st_atime_ns, version.st_mtime_ns))
        assert io._read_image(source)[0, 0] == 7
        assert io._read_image(other)[0, 0] == 2
        assert workspace.bytes == workspace.max_bytes == 4
        assert not cache.images and cache._bytes == 0
        assert io._read_image(source)[0, 0] == 7
    assert reads == [b'a', b'changed', b'bb', b'changed']


def test_entering_workspace_releases_prior_standalone_pixel_copies(tmp_path, monkeypatch):
    source = tmp_path / 'source.png'
    source.write_bytes(b'image')
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    monkeypatch.setattr(io, '_decode_pixels', lambda path: frame.copy())
    cache = io.ImageCache()
    with io.image_cache_scope(cache):
        io._read_image(source)
        assert cache._bytes == frame.nbytes
        with image_workspace_scope() as workspace:
            io._read_image(source)
            assert not cache.images and cache._bytes == 0
            assert workspace.bytes == frame.nbytes
