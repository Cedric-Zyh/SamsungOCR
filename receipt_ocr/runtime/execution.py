"""Per-run OCR reuse and inclusive timing; nothing is shared between receipts."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from hashlib import sha256
from pathlib import Path
import sys
from threading import Lock
from time import perf_counter

from .calls import record_recognition_cache_hit, record_model_initialization


_WINDOWS = sys.platform == 'win32'


@dataclass
class Execution:
    pages: dict = field(default_factory=dict)
    text_boxes: dict = field(default_factory=dict)
    steps: dict = field(default_factory=dict)
    cache_hits: int = 0
    text_box_cache_hits: int = 0
    timing_lock: Lock = field(default_factory=Lock)

    def snapshot(self):
        from ..imaging.working_images import current_workspace
        workspace = current_workspace()
        return {
            'steps': {name: {'calls': value['calls'], 'seconds': round(value['seconds'], 4)}
                      for name, value in self.steps.items()},
            'page_ocr_cache_hits': self.cache_hits,
            'text_box_ocr_cache_hits': self.text_box_cache_hits,
            **({'image_processing_cache': workspace.snapshot()} if workspace is not None else {}),
        }


_current = ContextVar('receipt_execution', default=None)


@contextmanager
def measure(name):
    execution = _current.get()
    initialization = name.endswith('_model_setup') or name == 'paddle_text_box_setup'
    if execution is None and not initialization:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        finished = perf_counter()
        if initialization:
            record_model_initialization(started, finished)
        if execution is not None:
            with execution.timing_lock:
                value = execution.steps.setdefault(name, {'seconds': 0.0, 'calls': 0})
                value['seconds'] += finished - started
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
            from ..imaging.working_images import image_workspace_scope
            with image_workspace_scope():
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
        record_recognition_cache_hit()
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
        record_recognition_cache_hit()


def reuse_text_boxes(source, model_signature, recognize):
    """Reuse identical model inputs within this run, after provider checks.

    Paths alone are unsafe: temporary paths can be rewritten, while identical
    direction-search images can have different paths. The model signature
    includes every inference option. Known pixels use their immutable content
    key; other files use encoded content. ``recognize`` returns the boxes and
    a cacheability flag, preserving providers' existing failure behavior.
    Only stable file versions and successful calls can populate the cache.
    """
    execution = _current.get()
    if execution is None:
        return recognize()
    from ..imaging.working_images import buffered_key
    pixels = buffered_key(source)
    if pixels is not None:
        key = (model_signature, ('bgr_pixels', pixels))
        if key in execution.text_boxes and buffered_key(source) == pixels:
            with execution.timing_lock:
                execution.text_box_cache_hits += 1
            record_recognition_cache_hit()
            return deepcopy(execution.text_boxes[key]), True
        result, cacheable = recognize()
        if cacheable and buffered_key(source) == pixels:
            execution.text_boxes[key] = deepcopy(result)
        return result, cacheable
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
            stable = (sha256(path.read_bytes()).digest() == content
                      and version == _file_version(path))
        except OSError:
            stable = False
        if not stable:
            return recognize()
        with execution.timing_lock:
            execution.text_box_cache_hits += 1
        record_recognition_cache_hit()
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
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    # Windows ctime is creation time on Python 3.11, and even ChangeTime can
    # collide for rapid writes. A journal sequence detects closed rewrites,
    # including A -> B -> A with restored content and timestamps.
    return (*version, _windows_file_usn(path)) if _WINDOWS else version


def _windows_file_usn(path):
    """Read the last journal sequence; unsupported filesystems disable reuse."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.DeviceIoControl
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                     ctypes.c_void_p]
    query.restype = wintypes.BOOL
    # READ_FILE_USN_DATA accepts both NTFS (V2) and ReFS (V3) records.
    versions = (wintypes.WORD * 2)(2, 3)
    record = ctypes.create_string_buffer(4096)
    returned = wintypes.DWORD()
    with open(str(path), 'rb') as source:
        handle = msvcrt.get_osfhandle(source.fileno())
        # FSCTL_READ_FILE_USN_DATA: query only; never creates a journal.
        if not query(handle, 0x000900EB, versions, ctypes.sizeof(versions),
                     record, ctypes.sizeof(record), ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    return _usn_from_record(record.raw[:returned.value])


def _usn_from_record(record):
    """Decode the fixed USN field, rejecting unavailable or unknown records."""
    import struct

    if len(record) < 8:
        raise OSError('File change journal record is incomplete')
    length, major, _ = struct.unpack_from('<IHH', record)
    offset, minimum = {2: (24, 60), 3: (40, 76)}.get(major, (0, 0))
    if not minimum or not minimum <= length <= len(record):
        raise OSError('File change journal record is unsupported or incomplete')
    usn = struct.unpack_from('<q', record, offset)[0]
    if usn <= 0:
        raise OSError('File change journal sequence is unavailable')
    return usn
