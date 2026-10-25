"""Per-run OCR reuse and inclusive timing; nothing is shared between receipts."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import perf_counter


@dataclass
class Execution:
    pages: dict = field(default_factory=dict)
    text_boxes: dict = field(default_factory=dict)
    steps: dict = field(default_factory=dict)
    cache_hits: int = 0
    text_box_cache_hits: int = 0
    timing_lock: Lock = field(default_factory=Lock)

    def snapshot(self):
        return {
            'steps': {name: {'calls': value['calls'], 'seconds': round(value['seconds'], 4)}
                      for name, value in self.steps.items()},
            'page_ocr_cache_hits': self.cache_hits,
            'text_box_ocr_cache_hits': self.text_box_cache_hits,
        }


_current = ContextVar('receipt_execution', default=None)


@contextmanager
def measure(name):
    execution = _current.get()
    if execution is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - started
        with execution.timing_lock:
            value = execution.steps.setdefault(name, {'seconds': 0.0, 'calls': 0})
            value['seconds'] += elapsed
            value['calls'] += 1


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def recognition_run(function):
    """Nested stages reuse their parent's context; a retry gets a fresh one."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _current.get() is not None:
            return function(*args, **kwargs)
        execution = Execution()
        token = _current.set(execution)
        try:
            result = function(*args, **kwargs)
            result['processing_timings'] = execution.snapshot()
            return result
        finally:
            _current.reset(token)
    return wrapped


def recognize_page(source, backend, recognize):
    """Only whole-page calls with default OCR parameters are reusable here.

    Provider permissions are checked even on a hit. Crops, parameter variants,
    failed calls and results from previous runs are never reused.
    """
    from .scope import provider_allowed
    if not provider_allowed(backend):
        return []
    execution = _current.get()
    key = (str(Path(source).resolve()), backend)
    if execution is not None and key in execution.pages:
        execution.cache_hits += 1
        return deepcopy(execution.pages[key])
    with measure('page_ocr'):
        rows = recognize(source, backend=backend)
    if execution is not None:
        execution.pages[key] = deepcopy(rows)
    return rows


def record_page_reuse():
    """Include explicitly shared document-context reads in the existing metric."""
    execution = _current.get()
    if execution is not None:
        with execution.timing_lock:
            execution.cache_hits += 1


def reuse_text_boxes(source, model_signature, recognize):
    """Reuse identical encoded inputs within this run, after provider checks.

    Paths alone are unsafe: temporary paths can be rewritten, while identical
    direction-search images can have different paths. The model signature
    includes every inference option; different encodings remain separate even
    when they might decode to the same pixels. ``recognize`` returns the boxes
    and a cacheability flag, preserving providers' existing failure behavior.
    Only stable file versions and successful calls can populate the cache.
    """
    execution = _current.get()
    if execution is None:
        return recognize()
    path = Path(source)
    try:
        version = _file_version(path)
        content = sha256(path.read_bytes()).digest()
        stable = version == _file_version(path)
    except OSError:
        return recognize()
    if not stable:
        return recognize()
    key = (model_signature, content)
    if key in execution.text_boxes:
        result = deepcopy(execution.text_boxes[key])
        try:
            stable = version == _file_version(path)
        except OSError:
            stable = False
        if not stable:
            return recognize()
        with execution.timing_lock:
            execution.text_box_cache_hits += 1
        return result, True
    result, cacheable = recognize()
    try:
        unchanged = (version == _file_version(path)
                     and sha256(path.read_bytes()).digest() == content
                     and version == _file_version(path))
    except OSError:
        unchanged = False
    if cacheable and unchanged:
        execution.text_boxes[key] = deepcopy(result)
    return result, cacheable


def _file_version(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
