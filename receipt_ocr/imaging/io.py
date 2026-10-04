"""Image decoding, per-run caching, and artifact writing."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections import OrderedDict
from threading import RLock
from pathlib import Path
from typing import Iterator
import cv2
import numpy as np


def _decode_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法解码图片: {path}")
    return image


@dataclass
class ImageCache:
    """Cache decoded source images for one recognition run.

    Callers receive a copy so a preprocessing step cannot mutate the cached
    source frame for a later stage.
    """

    max_bytes: int = 128 * 1024 * 1024
    images: OrderedDict = field(default_factory=OrderedDict)
    _bytes: int = 0
    _lock: RLock = field(default_factory=RLock)

    def read(self, path: str | Path) -> np.ndarray:
        source = Path(path).resolve()
        stat = source.stat()
        key = (str(source), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        with self._lock:
            if key in self.images:
                self.images.move_to_end(key)
                return self.images[key].copy()
            image = _decode_image(source)
            # Replaced files must not retain an old decoded version.
            for old in list(self.images):
                if old[0] == key[0]:
                    self._bytes -= self.images.pop(old).nbytes
            if image.nbytes <= self.max_bytes:
                while self.images and self._bytes + image.nbytes > self.max_bytes:
                    self._bytes -= self.images.popitem(last=False)[1].nbytes
                self.images[key] = image
                self._bytes += image.nbytes
            return image.copy()

    def clear(self) -> None:
        with self._lock:
            self.images.clear()
            self._bytes = 0


_active_cache: ContextVar[ImageCache | None] = ContextVar(
    "receipt_ocr_image_cache", default=None
)


@contextmanager
def image_cache_scope(cache: ImageCache) -> Iterator[ImageCache]:
    """Activate a cache only for the current recognition execution context."""

    if _active_cache.get() is cache:
        yield cache
        return
    token = _active_cache.set(cache)
    try:
        yield cache
    finally:
        _active_cache.reset(token)
        cache.clear()


def _read_image(path: str | Path) -> np.ndarray:
    cache = _active_cache.get()
    return cache.read(path) if cache is not None else _decode_image(path)


def read_image_size(path: str | Path) -> tuple[int, int]:
    """Return the source image ``(width, height)`` as the crop writers see it.

    EXIF rotation is already applied by the decoder, so the numbers here are in
    the same frame as :func:`save_region_crop` and the pixel boxes an external
    service reports.
    """
    image = _read_image(path)
    height, width = image.shape[:2]
    return width, height


def _write_stage_image(destination: str | Path, image: np.ndarray, suffix: str) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError(f"中间图片编码失败: {destination.name}")
    encoded.tofile(str(destination))
