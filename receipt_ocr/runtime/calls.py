"""Lightweight physical recognition calls for the current receipt processing.

The collector owns no images. Temporary inputs retain only their basename and
size; previews are links to artifacts that already exist within the run folder.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import perf_counter
from urllib.parse import quote, urlsplit

from .progress import MODEL_LABELS, STAGE_LABELS, current_progress_context
from .image_processing import ImageProcessingTimings, image_processing_scope, exclude_image_interval


_current = ContextVar('receipt_recognition_calls', default=None)
_active = ContextVar('receipt_active_recognition_call', default=None)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _safe_error(error):
    # Provider messages can contain credentials, signed URLs and local paths.
    # Keep useful failure types without persisting their untrusted message text.
    if isinstance(error, BaseException):
        return f'识别失败（{type(error).__name__}）'
    return '识别服务返回失败'


class RecognitionCalls:
    def __init__(self, source=None, artifact_dir=None, artifact_url_prefix=''):
        self.source = Path(source).resolve() if source else None
        self.artifact_dir = Path(artifact_dir).resolve() if artifact_dir else None
        self.artifact_url_prefix = str(artifact_url_prefix or '').rstrip('/')
        self.started_at = _now()
        self.finished_at = None
        self.processing_seconds = 0.0
        self.status = 'running'
        self.calls = []
        self.cache_hits = 0
        self._started = perf_counter()
        self._finished = None
        self._model_initializations = []
        self._image_processing = ImageProcessingTimings()
        self._lock = Lock()

    def input_metadata(self, source):
        metadata = {'input_name': '图片', 'width': 0, 'height': 0}
        if isinstance(source, (str, Path)):
            path = Path(source)
            metadata['input_name'] = path.name.replace('\\', '/').rsplit('/', 1)[-1]
            try:
                from ..imaging.working_images import cached_size
                from PIL import Image
                size = cached_size(path)
                if size is None:
                    with Image.open(path) as image:
                        size = image.size
                metadata['width'], metadata['height'] = size
            except (OSError, ValueError):
                pass
            prefix = self.artifact_url_prefix
            parsed = urlsplit(prefix)
            if (self.artifact_dir and prefix.startswith('/files/artifacts/')
                    and not parsed.scheme and not parsed.netloc
                    and not parsed.query and not parsed.fragment and '..' not in prefix.split('/')):
                try:
                    resolved = path.resolve()
                    relative = resolved.relative_to(self.artifact_dir)
                    if resolved.is_file():
                        metadata['image_url'] = prefix + '/' + quote(relative.as_posix(), safe='/')
                except (OSError, ValueError):
                    pass
        elif getattr(source, 'shape', None) is not None:
            metadata['height'], metadata['width'] = map(int, source.shape[:2])
        elif getattr(source, 'size', None) is not None and isinstance(source.size, tuple):
            metadata['width'], metadata['height'] = source.size
        elif isinstance(source, (list, tuple)):
            metadata['input_name'] = f'文字行图片（{len(source)} 张）'
        return metadata

    def start_call(self, method, action, source):
        context = current_progress_context()
        stage = context.get('target_id') or context.get('stage') or 'recognition'
        row = dict(stage=stage, stage_label=STAGE_LABELS.get(stage, context.get('target') or '识别'),
                   method=method, method_label=MODEL_LABELS.get(method, method), action=action,
                   **self.input_metadata(source), started_at=_now(), seconds=0.0, status='running')
        with self._lock:
            row['id'] = len(self.calls) + 1
            self.calls.append(row)
        return RecognitionCall(self, row)

    def finish(self, status):
        with self._lock:
            self.finished_at = _now()
            self._finished = perf_counter()
            self.processing_seconds = round(max(0, self._finished - self._started), 4)
            self.status = status

    def snapshot(self):
        with self._lock:
            end = self._finished if self._finished is not None else perf_counter()
            total = round(max(0, end - self._started), 4)
            # Count wall time once even when initialization intervals overlap.
            intervals = sorted((max(self._started, start), min(end, stop))
                               for start, stop in self._model_initializations
                               if stop > self._started and start < end)
            initialized, last = 0.0, self._started
            for start, stop in intervals:
                initialized += max(0, stop - max(last, start))
                last = max(last, stop)
            initialized = round(min(total, initialized), 4)
            return deepcopy(dict(version=3, started_at=self.started_at, finished_at=self.finished_at,
                                 processing_seconds=total,
                                 model_initialization_seconds=initialized,
                                 recognition_seconds=round(max(0, total - initialized), 4), status=self.status,
                                 summary=dict(total=len(self.calls),
                                              succeeded=sum(row['status'] == 'succeeded' for row in self.calls),
                                              failed=sum(row['status'] == 'failed' for row in self.calls),
                                              cache_hits=self.cache_hits), calls=self.calls,
                                 image_processing=self._image_processing.snapshot()))


class RecognitionCall:
    def __init__(self, collector=None, row=None):
        self.collector = collector
        self.row = row
        self._started = perf_counter()

    def fail(self, error=None):
        if self.collector is not None:
            with self.collector._lock:
                self.row.update(status='failed', error=_safe_error(error))

    def resume(self):
        """Extend an accepted async request through its existing result query."""
        if self.collector is not None:
            with self.collector._lock:
                if self.row["status"] == "succeeded":
                    self.row["status"] = "running"

    def finish(self):
        if self.collector is not None:
            finished = perf_counter()
            exclude_image_interval(self._started, finished)
            with self.collector._lock:
                self.row['seconds'] = round(max(0, finished - self._started), 4)
                if self.row['status'] == 'running':
                    self.row['status'] = 'succeeded'


@contextmanager
def capture_recognition_calls(source=None, artifact_dir=None, artifact_url_prefix=''):
    """Nested captures share the enclosing run; copy_context shares its lock too."""
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    collector = RecognitionCalls(source, artifact_dir, artifact_url_prefix)
    token = _current.set(collector)
    failure = None
    try:
        with image_processing_scope(collector._image_processing):
            yield collector
    except BaseException as exc:
        failure = exc
        raise
    finally:
        collector.finish('failed' if failure is not None else 'completed')
        _current.reset(token)
        if failure is not None:
            try:
                failure.recognition_calls = collector.snapshot()
            except (AttributeError, TypeError):
                pass


@contextmanager
def recognition_call(method, action, source=None):
    """Place immediately around an actual prediction or logical remote request."""
    collector = _current.get()
    active = _active.get()
    if collector is None or (active and active[0] is collector and active[1] == method):
        yield active[2] if active and active[0] is collector else RecognitionCall()
        return
    invocation = collector.start_call(method, action, source)
    token = _active.set((collector, method, invocation))
    try:
        yield invocation
    except BaseException as exc:
        invocation.fail(exc)
        raise
    finally:
        invocation.finish()
        _active.reset(token)


def record_recognition_cache_hit():
    collector = _current.get()
    if collector is not None:
        with collector._lock:
            collector.cache_hits += 1


def record_model_initialization(started, finished):
    """Keep model preparation separate without adding an OCR invocation."""
    collector = _current.get()
    if collector is not None and finished > started:
        exclude_image_interval(started, finished)
        with collector._lock:
            collector._model_initializations.append((started, finished))


def check_prediction_errors(invocation, predictions):
    """Some model APIs return an error object instead of raising an exception."""
    if any(result.get('error') for result in predictions):
        invocation.fail()
