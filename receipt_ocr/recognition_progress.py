"""Per-job progress reporting, independent of the OCR result and request context."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import logging

from .database import now_iso


_reporter = ContextVar('recognition_progress_reporter', default=None)
_pause_checker = ContextVar('recognition_pause_checker', default=None)


@contextmanager
def progress_reporting(callback, *, paused=None):
    token = _reporter.set(callback)
    pause_token = _pause_checker.set(paused)
    try:
        yield
    finally:
        _pause_checker.reset(pause_token)
        _reporter.reset(token)


def recognition_paused():
    check = _pause_checker.get()
    return bool(check and check())


def report_dzt_progress(stage, **details):
    callback = _reporter.get()
    if callback is None:
        return
    try:
        callback({'provider': 'danzhengtong', 'stage': stage, 'updated_at': now_iso(), **details})
    except Exception:
        # A transient progress write must not fail or repeat a provider request.
        logging.getLogger(__name__).exception('Unable to save recognition progress')


def public_progress(job):
    import json
    progress = json.loads(job.get('progress_json') or '{}')
    if not progress:
        return {}
    end = now_iso() if job['status'] == 'running' else job.get('finished_at') or progress['updated_at']
    progress['elapsed_seconds'] = max(0, int((datetime.fromisoformat(end)
                                            - datetime.fromisoformat(progress['started_at'])).total_seconds()))
    return progress
