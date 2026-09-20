"""Per-job progress reporting, independent of the OCR result and request context."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import logging
from time import perf_counter

from .database import now_iso


_reporter = ContextVar('recognition_progress_reporter', default=None)
_pause_checker = ContextVar('recognition_pause_checker', default=None)
_progress_context = ContextVar('recognition_progress_context', default={})


@contextmanager
def progress_reporting(callback, *, paused=None):
    token = _reporter.set(callback)
    pause_token = _pause_checker.set(paused)
    try:
        yield
    finally:
        _pause_checker.reset(pause_token)
        _reporter.reset(token)


@contextmanager
def progress_context(**details):
    """Attach model/stage metadata to provider progress events."""
    token = _progress_context.set({**_progress_context.get(), **details})
    try:
        yield
    finally:
        _progress_context.reset(token)


def recognition_paused():
    check = _pause_checker.get()
    return bool(check and check())


def report_dzt_progress(stage, **details):
    callback = _reporter.get()
    if callback is None:
        return
    try:
        callback({**_progress_context.get(), 'provider': 'danzhengtong',
                  'stage': stage, 'updated_at': now_iso(), **details})
    except Exception:
        # A transient progress write must not fail or repeat a provider request.
        logging.getLogger(__name__).exception('Unable to save recognition progress')


STAGE_LABELS = dict(routing="整页文字与版式", fields="印刷字段", products="商品明细",
                    handwriting="手写签名", date="签收日期", seal="客户印章",
                    finalizing="结果核验与整理", saving="保存结果")
MODEL_LABELS = dict(paddle="Paddle Mobile", paddle_server="Paddle Server",
                    paddle_v6="Paddle v6 Small", paddle_seal="Paddle 印章专用",
                    qingtong="清瞳", danzhengtong="单证通", system="系统")


def report_model_progress(stage, phase, **details):
    callback = _reporter.get()
    if callback is None:
        return
    try:
        callback({**_progress_context.get(), 'provider': 'model', 'stage': stage,
                  'phase': phase, 'updated_at': now_iso(), **details})
    except Exception:
        logging.getLogger(__name__).exception('Unable to save recognition progress')


def report_plan(config):
    steps = [dict(key=f"{stage}:{method}", target_id=stage, target=STAGE_LABELS[stage],
                  method=method, method_label=MODEL_LABELS.get(method, method), phase='pending')
             for stage, methods in config.items() if stage in STAGE_LABELS for method in methods]
    report_model_progress('preparing', 'started', steps=steps, target='准备识别', method='system')


@contextmanager
def model_stage(stage, method, *, channel='main', message=''):
    with progress_context(method=method, method_label=MODEL_LABELS.get(method, method),
                          target=STAGE_LABELS[stage], target_id=stage, channel=channel):
        started = perf_counter()
        report_model_progress(stage, 'started', message=message)
        try:
            yield
        except Exception:
            # Detailed provider errors already have their own sanitized reporting.
            report_model_progress(stage, 'failed', elapsed_seconds=round(perf_counter() - started, 2),
                                  message='本步骤执行失败，详情见识别结果')
            raise
        else:
            report_model_progress(stage, 'completed', elapsed_seconds=round(perf_counter() - started, 2),
                                  message=message)


@contextmanager
def model_operation(method, action):
    """Short-lived physical model activity; no per-crop history rows."""
    context = _progress_context.get()
    if not context or _reporter.get() is None:
        yield
        return
    report_model_progress(context['target_id'], 'operation',
                          operation={'method': method, 'method_label': MODEL_LABELS.get(method, method),
                                     'action': action, 'started_at': now_iso()})
    try:
        yield
    finally:
        report_model_progress(context['target_id'], 'operation', operation=None)


def seconds_between(start, end):
    if not start or not end:
        return 0
    return max(0, round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1))


def public_progress(job):
    import json
    progress = json.loads(job.get('progress_json') or '{}')
    if not progress:
        return {}
    end = now_iso() if job['status'] == 'running' else job.get('finished_at') or progress['updated_at']
    for current in [progress, progress.get('parallel', {})]:
        if current.get('started_at') and current.get('phase') not in {'completed', 'failed'}:
            current['elapsed_seconds'] = seconds_between(current['started_at'], end)
        operation = current.get('operation')
        if operation:
            operation['elapsed_seconds'] = seconds_between(operation['started_at'], end)
    for step in progress.get('steps', []):
        if step.get('phase') == 'started':
            step['elapsed_seconds'] = seconds_between(step.get('started_at'), end)
    return progress


def merge_progress(previous, event):
    """Merge foreground, physical-model and concurrent remote activity atomically."""
    parallel = event.get('channel') == 'parallel'
    current = previous.get('parallel', {}) if parallel else previous
    same = (current.get('provider'), current.get('stage'), current.get('method')) == (
        event.get('provider'), event.get('stage'), event.get('method'))
    same_wait = (current.get('provider') == event.get('provider') == 'danzhengtong'
                 and {current.get('stage'), event.get('stage')} <= {'waiting', 'paused'})
    if event.get('phase') == 'operation':
        updated = {**current, 'operation': event['operation'], 'updated_at': event['updated_at']}
        log = False
    else:
        updated = {**event, 'started_at': current['started_at'] if (same or same_wait) and current.get('started_at') else event['updated_at']}
        log = not (same or same_wait) or current.get('phase') != event.get('phase')
    if parallel:
        return {**previous, 'parallel': updated}, log
    if previous.get('parallel'):
        updated['parallel'] = previous['parallel']
    steps = [dict(step) for step in event.get('steps', previous.get('steps', []))]
    if event.get('provider') == 'model' and event.get('phase') in {'started', 'completed', 'failed'}:
        key = f"{event['stage']}:{event.get('method')}"
        if event['stage'] == 'routing' and not any(step['key'] == key for step in steps):
            steps.insert(0, dict(key=key, target=event.get('target'), method=event.get('method'),
                                 method_label=event.get('method_label'), target_id='routing'))
        for step in steps:
            if step['key'] == key:
                step.update(phase=event['phase'], message=event.get('message', ''))
                if event['phase'] == 'started':
                    step['started_at'] = event['updated_at']
                else:
                    step['elapsed_seconds'] = event.get('elapsed_seconds', 0)
    if steps:
        updated['steps'] = steps
    return updated, log
