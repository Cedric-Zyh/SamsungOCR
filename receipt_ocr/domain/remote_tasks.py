"""Pure remote continuation rules, independent of workers and SQL storage.

The page and each stamp own a branch. Parent status and dispatch fields are
projections; neither may erase an accepted UUID or authorize a repeated POST.
Times are supplied by the caller so recovery and scheduling are deterministic.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import json


def branch_due(value, timestamp):
    """Missing legacy schedules are due, never an instruction to submit again."""
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return True


def uncertain_submission(item, call):
    return bool(item.get('submission_unknown') or item.get('submission_started') or item.get('_submission_started')
                or item.get('submitted_at') or item.get('submission')
                or call.get('recognition_invoked') or call.get('submission_unknown')
                or (call.get('transport') or {}).get('phase') in {'submitting', 'submit_finished'})


def remote_state(task, *, recover=False):
    """Upgrade a durable continuation without inventing another recognition POST."""
    state = deepcopy(json.loads(task.get('seal_requests_json') or '{}'))
    legacy_page = not state
    if legacy_page:
        # Empty JSON is the old single-scene protocol, including its seal text.
        state = {'prepared': True, 'requests': [], 'page_required': True, 'seal_required': False}
    state.setdefault('seal_required', True)
    state.setdefault('prepared', False)
    state.setdefault('requests', [])
    state.setdefault('page_required', True)
    page = state.get('page')
    call = json.loads(task.get('recognition_call_json') or '{}')
    if not isinstance(page, dict):
        phase = (call.get('transport') or {}).get('phase', '')
        if not state['page_required']:
            status = 'skipped'
        elif json.loads(task.get('result_json') or '{}'):
            status = 'completed'
        elif task.get('req_uuid'):
            status = 'waiting'
        elif task.get('status') == 'failed' and (legacy_page or call):
            status = 'failed'
        elif ((legacy_page and task.get('status') in {'unknown', 'submitting'}) or task.get('submitted_at')
              or json.loads(task.get('submission_json') or '{}') or phase in {'submitting', 'submit_finished'}):
            status = 'unknown'
        else:
            status = 'queued'
        page = {'status': status, 'error': task.get('error_message', '') if status in {'failed', 'unknown'} else '',
                'next_query_at': task.get('next_query_at', '') if status == 'waiting' else ''}
        state['page'] = page
    # A persisted UUID/result takes precedence over stale queued/submitting markers.
    if task.get('req_uuid') and page.get('status') not in {'completed', 'skipped'}:
        page['status'] = 'completed' if json.loads(task.get('result_json') or '{}') else 'waiting'
    elif (page.get('status') in {'queued', 'uploading', 'waiting'} and not task.get('req_uuid')
          and (uncertain_submission(task, call) or page.get('status') == 'waiting')):
        page.update(status='unknown', error=page.get('error') or '整页提交状态待确认，保留原请求，不重复提交')
    if recover and page.get('status') == 'uploading':
        page['status'] = 'queued'  # The recognition POST has not started.
    if recover and page.get('status') == 'submitting' and not task.get('req_uuid'):
        page.update(status='unknown', error='整页提交期间服务中断，受理状态待确认；请勿重复提交')
    page.setdefault('next_query_at', '')
    page.setdefault('error', '')
    for child in state['requests']:
        if child.get('req_uuid') and child.get('status') not in {'completed', 'received', 'skipped'}:
            child['status'] = 'waiting'
        elif (not child.get('req_uuid') and child.get('status') in {'queued', 'uploading', 'waiting'}
              and (uncertain_submission(child, child.get('call') or {}) or child.get('status') == 'waiting')):
            child.update(status='unknown', error=child.get('error') or '印章提交状态待确认，保留原请求，不重复提交')
        if recover and child.get('status') == 'uploading':
            child['status'] = 'queued'
        if recover and child.get('status') == 'submitting' and not child.get('req_uuid'):
            child.update(status='unknown', error='印章提交期间服务中断，受理状态待确认；请勿重复提交')
        child.setdefault('next_query_at', task.get('next_query_at', '') if child.get('status') == 'waiting' else '')
    state['version'] = 2
    return state


def aggregate_remote(state):
    """A parent's status/deadline summarizes independent branches, never gates them."""
    page = state['page']
    children = state['requests']
    prepare_pending = state.get('seal_required') and not state['prepared']
    branches = ([page] if state.get('page_required') else []) + children
    queued = prepare_pending or any(item.get('status') in {'queued', 'uploading', 'submitting'} for item in branches)
    waiting = [item for item in branches if item.get('status') == 'waiting']
    state['complete'] = not queued and not waiting
    success = any(item.get('status') in {'completed', 'received'} for item in branches)
    unknown = any(item.get('status') == 'unknown' for item in branches)
    failure = any(item.get('status') == 'failed' for item in branches)
    status = ('queued' if queued else 'waiting' if waiting else
              'unknown' if unknown and not success else
              'failed' if failure and not success and state.get('page_required') else 'received')
    due = min((item.get('next_query_at') or '' for item in waiting), default='')
    errors = ([f"整页：{page['error']}"] if page.get('error') else [])
    if state.get('preparation_error'):
        errors.append('印章准备：' + state['preparation_error'])
    errors.extend(f"印章{item.get('index', 0) + 1}：{item['error']}" for item in children if item.get('error'))
    return {'status': status, 'next_query_at': due, 'error_message': '；'.join(errors)[:1200]}


def next_submission(state):
    """Choose one unsent branch; accepted/uncertain branches never enter here."""
    if state.get('page_required') and state['page'].get('status') == 'queued':
        return 'page_submit', None
    if state.get('seal_required') and not state['prepared']:
        return 'seal_prepare', None
    return next((('seal_submit', index) for index, child in enumerate(state['requests'])
                 if child.get('status') == 'queued'), None)


def next_remote_operation(task, state, control, *, timestamp, prefer_submit=False):
    """Choose one ready operation while retaining the parent lease as sole writer."""
    manual = bool(task.get('manual_query'))
    queries = []
    page = state['page']
    if (state.get('page_required') and page.get('status') == 'waiting' and task.get('req_uuid')
            and (manual or branch_due(page.get('next_query_at'), timestamp))):
        queries.append((page.get('last_query_at') or '', 'page_query', None))
    for index, child in enumerate(state['requests']):
        if (child.get('status') == 'waiting' and child.get('req_uuid')
                and (manual or branch_due(child.get('next_query_at'), timestamp))):
            queries.append((child.get('last_query_at') or '', 'seal_query', index))
    queries.sort(key=lambda item: item[0])
    query = queries[0][1:] if queries and (manual or control['auto_query']) else None
    submit = None
    if not control['submit_paused'] and not manual:
        submit = next_submission(state)
    return (submit or query) if prefer_submit else (query or submit)


def begin_upload():
    return {'status': 'uploading', 'error': ''}


def begin_submission():
    """Persist before POST: an interrupted submit cannot be retried as unsent."""
    return {'status': 'submitting'}


def accept_submission(*, next_query_at):
    return {'status': 'waiting', 'error': '', 'next_query_at': next_query_at}


def fail_submission(error, *, submitted, rejected=False):
    """A lost response is uncertain unless the provider explicitly rejected it."""
    return {'status': 'unknown' if submitted and not rejected else 'failed',
            'error': error, 'next_query_at': ''}


def fail_operation(branch, *, req_uuid, error, retry_at):
    """An accepted branch remains queryable even if its worker step fails."""
    return {'status': 'waiting' if req_uuid else
                      'unknown' if branch.get('status') == 'submitting' else 'failed',
            'error': error, 'next_query_at': retry_at if req_uuid else ''}


def finish_query(*, submitted_at, observed_at, completed, error=''):
    """The shared page/stamp polling policy; no clock or request is read here."""
    due = ''
    if not completed:
        current = datetime.fromisoformat(observed_at)
        elapsed = (current - datetime.fromisoformat(submitted_at)).total_seconds()
        delay = 2 if elapsed < 60 else 30 if elapsed < 600 else 120
        due = (current + timedelta(seconds=max(30, delay) if error else delay)).isoformat(timespec='seconds')
    return {'status': 'completed' if completed else 'waiting', 'error': error,
            'last_query_at': observed_at, 'next_query_at': due}
