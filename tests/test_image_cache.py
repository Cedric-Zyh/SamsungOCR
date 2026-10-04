from pathlib import Path

import numpy as np
import pytest

from receipt_ocr.imaging import io


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
