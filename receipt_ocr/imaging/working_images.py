"""Bounded, request-local pixel/transform reuse; evidence files remain real files."""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
from threading import RLock

import cv2
import numpy as np
from PIL import Image
from receipt_ocr.runtime.image_processing import image_operation

_active = ContextVar("receipt_working_images", default=None)
_disabled = ContextVar("receipt_working_images_disabled", default=False)
_unknown_version = object()


def _version(path):
    # Shares the Windows ChangeTime guard used by OCR-result reuse.
    from ..runtime.execution import _file_version

    try:
        return _file_version(path)
    except FileNotFoundError:
        return None
    except OSError:
        # Unavailable cache metadata must not replace the original image read.
        return _unknown_version


def _pixel_key(image):
    image = np.ascontiguousarray(image)
    return ("pixels", image.dtype.str, image.shape, sha256(memoryview(image)).digest())


class ImageWorkspace:
    def __init__(self, max_bytes=96 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.frames = OrderedDict()
        self.paths = OrderedDict()
        self.transforms = OrderedDict()
        self.bytes = 0
        self.lock = RLock()
        self.stats = dict(
            decoded_cache_hits=0,
            transform_cache_hits=0,
            rotation_computations=0,
            resize_computations=0,
            file_reads=0,
            file_writes=0,
            writes_avoided=0,
            virtual_images=0,
            memory_spills=0,
            array_inference_inputs=0,
            peak_bytes=0,
        )

    def _spill(self, key):
        # A temporary thumbnail must remain readable if memory pressure evicts
        # it before its caller consumes it. Never recreate a removed temp dir.
        for (name, mode), (version, frame) in list(self.paths.items()):
            if frame != key or version is not None or mode != "bgr":
                continue
            path = Path(name)
            if path.parent.is_dir() and not path.exists():
                if not cv2.imwrite(str(path), self.frames[key]):
                    raise ValueError("临时识别图片保存失败")
                self.stats["file_writes"] += 1
                self.stats["memory_spills"] += 1
                self.paths[(name, mode)] = (_version(path), frame)

    def _put(self, image):
        # Reader modes live in path aliases. Identical arrays need only one
        # retained copy, even when both a transform and a file refer to them.
        key = _pixel_key(image)
        if key in self.frames:
            self.frames.move_to_end(key)
            return key
        if image.nbytes > self.max_bytes:
            return None
        while self.frames and self.bytes + image.nbytes > self.max_bytes:
            oldest = next(iter(self.frames))
            self._spill(oldest)
            self.bytes -= self.frames.pop(oldest).nbytes
        stored = np.ascontiguousarray(image).copy()
        stored.flags.writeable = False
        self.frames[key] = stored
        self.bytes += stored.nbytes
        self.stats["peak_bytes"] = max(self.stats["peak_bytes"], self.bytes)
        return key

    def _alias(self, path, mode, version, key):
        if key is None or version is _unknown_version:
            return
        name = (str(Path(path).resolve()), mode)
        self.paths[name] = (version, key)
        self.paths.move_to_end(name)
        while len(self.paths) > 4096:
            old, (old_version, frame) = next(iter(self.paths.items()))
            if old_version is None and frame in self.frames:
                self._spill(frame)
            self.paths.pop(old, None)

    def peek(self, path, mode="bgr"):
        with self.lock:
            alias = (str(Path(path).resolve()), mode)
            entry = self.paths.get(alias)
            if entry is None:
                return None
            version, key = entry
            if version != _version(Path(path)) or key not in self.frames:
                self.paths.pop(alias, None)
                return None
            self.frames.move_to_end(key)
            self.paths.move_to_end(alias)
            self.stats["decoded_cache_hits"] += 1
            return self.frames[key].copy()

    def key(self, path, mode="bgr"):
        with self.lock:
            entry = self.paths.get((str(Path(path).resolve()), mode))
            if (
                entry is None
                or entry[1] not in self.frames
                or entry[0] != _version(Path(path))
            ):
                return None
            return entry[1]

    def remember(self, path, image, mode="bgr", *, virtual=False):
        with self.lock:
            key = self._put(image)
            self._alias(path, mode, None if virtual else _version(Path(path)), key)
            return key

    def cv_read(self, path, flags=cv2.IMREAD_COLOR, *, decoder=None):
        mode = (
            "bgr"
            if flags == cv2.IMREAD_COLOR
            else ("gray" if flags == cv2.IMREAD_GRAYSCALE else str(flags))
        )
        with self.lock:
            cached = self.peek(path, mode)
            if cached is not None:
                return cached
            before = _version(Path(path))
            image = (
                decoder(path) if decoder is not None else cv2.imread(str(path), flags)
            )
            self.stats["file_reads"] += 1
            if (
                image is not None
                and before is not None
                and before == _version(Path(path))
            ):
                self.remember(path, image, mode)
            return image

    def pil_read(self, path):
        with self.lock:
            cached = self.peek(path, "rgb")
            if cached is not None:
                return Image.fromarray(cached)
            bgr = self.peek(path)
            if bgr is not None:
                # Cross-reader reuse is only enabled for our generated PNGs,
                # which have no EXIF rotation or colour-profile instructions.
                alias = self.paths.get((str(Path(path).resolve()), "generated"))
                if alias is not None and alias[0] == _version(Path(path)):
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    self.remember(path, rgb, "rgb", virtual=alias[0] is None)
                    return Image.fromarray(rgb)
            before = _version(Path(path))
            with Image.open(path) as original:
                image = original.convert("RGB")
            self.stats["file_reads"] += 1
            if before is not None and before == _version(Path(path)):
                self.remember(path, np.asarray(image), "rgb")
            return image

    def transform(self, image, kind, parameters, factory):
        with self.lock:
            key = (kind, parameters, _pixel_key(image))
            frame = self.transforms.get(key)
            if frame in self.frames:
                self.frames.move_to_end(frame)
                self.transforms.move_to_end(key)
                self.stats["transform_cache_hits"] += 1
                return self.frames[frame].copy()
            result = factory()
            if kind in {"rotation", "resize"}:
                self.stats[f"{kind}_computations"] += 1
            stored = self._put(result)
            if stored is not None:
                self.transforms[key] = stored
                self.transforms.move_to_end(key)
                while len(self.transforms) > 2048:
                    self.transforms.popitem(last=False)
            return result

    def snapshot(self):
        with self.lock:
            return {**self.stats, "max_bytes": self.max_bytes}

    def clear(self):
        with self.lock:
            self.frames.clear()
            self.paths.clear()
            self.transforms.clear()
            self.bytes = 0


@contextmanager
def image_workspace_scope(*, enabled=True, max_bytes=96 * 1024 * 1024):
    if not enabled:
        token = _disabled.set(True)
        try:
            yield None
        finally:
            _disabled.reset(token)
        return
    if _disabled.get() or _active.get() is not None:
        yield None if _disabled.get() else _active.get()
        return
    workspace = ImageWorkspace(max_bytes)
    token = _active.set(workspace)
    try:
        yield workspace
    finally:
        _active.reset(token)
        workspace.clear()


def current_workspace():
    return None if _disabled.get() else _active.get()


def read_cv(path, flags=cv2.IMREAD_COLOR):
    workspace = current_workspace()
    return workspace.cv_read(path, flags) if workspace else cv2.imread(str(path), flags)


def open_rgb(path):
    workspace = current_workspace()
    return workspace.pil_read(path) if workspace else Image.open(path)


def remember_png(path, image):
    workspace = current_workspace()
    if (
        workspace is None
        or image.dtype != np.uint8
        or Path(path).suffix.lower() != ".png"
    ):
        return
    # Store only lossless 8-bit images; JPEG crops intentionally retain their
    # original encode/decode step and are never replaced by pre-encoding pixels.
    if image.ndim == 2:
        workspace.remember(path, image, "gray")
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 3:
        key = workspace.remember(path, image)
        with workspace.lock:
            workspace._alias(path, "generated", _version(Path(path)), key)


def write_cv(path, image):
    workspace = current_workspace()
    path = Path(path)
    if workspace is not None and path.is_file() and path.suffix.lower() == ".png":
        cached = workspace.peek(path, "gray" if image.ndim == 2 else "bgr")
        if cached is not None and np.array_equal(cached, image):
            workspace.stats["writes_avoided"] += 1
            return True
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), image)
    if ok and workspace is not None:
        workspace.stats["file_writes"] += 1
        remember_png(path, image)
    return ok


def write_pil(path, image):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    workspace = current_workspace()
    if workspace is not None:
        workspace.stats["file_writes"] += 1
        if image.mode in {"RGB", "L"}:
            array = np.asarray(image)
            remember_png(
                path,
                (
                    cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
                    if image.mode == "RGB"
                    else array
                ),
            )


def write_temporary(path, image):
    """Buffer scratch pixels for a caller known to accept ndarray inputs."""
    workspace = current_workspace()
    if workspace is not None:
        if image.dtype == np.uint8 and image.ndim == 3 and image.shape[2] == 3:
            path = Path(path)
            with workspace.lock:
                # Only fresh scratch names are virtual; existing files use
                # the ordinary writer so stale bytes cannot survive a rewrite.
                if (
                    not path.exists()
                    and workspace.remember(path, image, virtual=True) is not None
                ):
                    alias = workspace.paths[(str(path.resolve()), "bgr")]
                    workspace._alias(path, "generated", None, alias[1])
                    workspace.stats["virtual_images"] += 1
                    return
    if not write_cv(path, image):
        raise ValueError("方向检测缩略图保存失败")


def copy_image(source, destination):
    import shutil

    workspace = current_workspace()
    generated = workspace.key(source, "generated") if workspace is not None else None
    pixels = buffered_pixels(source)
    if not Path(source).is_file() and pixels is not None:
        if not write_cv(destination, pixels):
            raise ValueError("识别核对图片保存失败")
        return destination
    shutil.copyfile(source, destination)
    if pixels is not None:
        if generated is not None:
            remember_png(destination, pixels)
        else:
            workspace.remember(destination, pixels)
    return destination


def cached_transform(image, kind, parameters, factory):
    workspace = current_workspace()
    def calculate():
        # Only time real transforms; retrieving existing pixels is not a
        # second rotation or resize computation.
        label = {"rotation": "旋转校正", "resize": "缩放调整"}.get(kind, "图像变换")
        with image_operation(kind, label):
            return factory()
    return (
        workspace.transform(image, kind, parameters, calculate)
        if workspace
        else calculate()
    )


def buffered_pixels(path):
    workspace = current_workspace()
    return workspace.peek(path) if workspace else None


def buffered_key(path):
    workspace = current_workspace()
    return workspace.key(path) if workspace else None


def predictor_input(path):
    image = buffered_pixels(path)
    if image is None:
        return str(path)
    current_workspace().stats["array_inference_inputs"] += 1
    return image


def cached_size(path):
    key = buffered_key(path)
    return (key[2][1], key[2][0]) if key is not None else None
