"""Small per-run aggregates for image work, independent of OCR call counts.

Nested operations contribute their own exclusive time. The enclosing operation
does not count that same interval again; parallel threads keep independent
stacks, so their cumulative work can exceed elapsed wall time.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock, get_ident
from time import perf_counter

from .progress import STAGE_LABELS, current_progress_context


_current = ContextVar('receipt_image_processing', default=None)
_frames = ContextVar('receipt_image_processing_frames', default=())


def _covered(intervals, start, end):
    covered, last = 0.0, start
    for left, right in sorted((max(start, left), min(end, right))
                              for left, right in intervals if right > start and left < end):
        covered += max(0.0, right - max(last, left))
        last = max(last, right)
    return min(max(0.0, end - start), covered)


class ImageProcessingTimings:
    def __init__(self):
        self._steps = {}
        self._lock = Lock()

    def record(self, stage, stage_label, operation, label, seconds, failed):
        with self._lock:
            row = self._steps.setdefault((stage, operation), dict(
                stage=stage, stage_label=stage_label, operation=operation, label=label,
                calls=0, seconds=0.0, failed=0))
            row['calls'] += 1
            row['seconds'] += max(0.0, seconds)
            row['failed'] += int(failed)

    def snapshot(self):
        with self._lock:
            rows = [{**row, 'seconds': round(row['seconds'], 4)}
                    for row in self._steps.values()]
            return dict(seconds=round(sum(row['seconds'] for row in self._steps.values()), 4),
                        steps=rows)


@contextmanager
def image_processing_scope(recorder):
    token = _current.set(recorder)
    stack_token = _frames.set(())
    try:
        yield
    finally:
        _frames.reset(stack_token)
        _current.reset(token)


def _active_frame(recorder):
    frames = _frames.get()
    if frames:
        frame = frames[-1]
        # copy_context passes the recorder to parallel workers, but a worker's
        # image operation must not mutate another thread's active stack.
        if frame['recorder'] is recorder and frame['thread'] == get_ident():
            return frame
    return None


def exclude_image_interval(started, finished):
    """Exclude OCR/setup performed inside a measured image operation."""
    recorder = _current.get()
    frame = _active_frame(recorder) if recorder is not None else None
    if frame is not None and finished > started:
        frame['excluded'].append((started, finished))


@contextmanager
def image_operation(operation, label):
    recorder = _current.get()
    if recorder is None:
        yield
        return
    context = current_progress_context()
    stage = context.get('target_id') or context.get('stage') or 'other'
    stage_label = STAGE_LABELS.get(stage, context.get('target') or '其他处理')
    parent = _active_frame(recorder)
    started = perf_counter()
    frame = dict(recorder=recorder, thread=get_ident(), excluded=[])
    stack = _frames.get() if parent is not None else ()
    token = _frames.set((*stack, frame))
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        finished = perf_counter()
        _frames.reset(token)
        elapsed = max(0.0, finished - started)
        elapsed -= _covered(frame['excluded'], started, finished)
        recorder.record(stage, stage_label, operation, label, elapsed, failed)
        if parent is not None:
            parent['excluded'].append((started, finished))


def timed_image(operation, label):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with image_operation(operation, label):
                return function(*args, **kwargs)
        return wrapped
    return decorate
