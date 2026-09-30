from pathlib import Path

import numpy as np

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
