"""Per-run OCR reuse and inclusive timing; nothing is shared between receipts."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from threading import Lock
from time import perf_counter


@dataclass
class Execution:
    pages: dict = field(default_factory=dict)
    steps: dict = field(default_factory=dict)
    cache_hits: int = 0
    timing_lock: Lock = field(default_factory=Lock)

    def snapshot(self):
        return {
            'steps': {name: {'calls': value['calls'], 'seconds': round(value['seconds'], 4)}
                      for name, value in self.steps.items()},
            'page_ocr_cache_hits': self.cache_hits,
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
    from .recognition_scope import provider_allowed
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
