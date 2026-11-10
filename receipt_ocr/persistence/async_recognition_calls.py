"""Merge one durable asynchronous recognition with the retained physical calls."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from math import isfinite


def _seconds(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _elapsed(started_at, finished_at):
    try:
        seconds = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
        return round(seconds, 4) if seconds >= 0 else None
    except (TypeError, ValueError):
        return None


def transport_timing(call, remote):
    """Keep legacy measurements partial; recorded lifecycle timestamps are separate."""
    saved = call.get('transport')
    if isinstance(saved, dict):
        return deepcopy(saved)
    started = remote.get('submitted_at') or ''
    finished = (remote.get('last_query_at') or ''
                if remote.get('status') in {'received', 'applied', 'conflict'} else '')
    return {'upload_seconds': None, 'submit_seconds': None, 'query_seconds': None,
            'query_count': remote.get('query_count'), 'request_seconds': None,
            'measured_request_seconds': _seconds(call.get('seconds')),
            'elapsed_seconds': _elapsed(started, finished), 'elapsed_scope': 'submission_to_result',
            'started_at': started, 'finished_at': finished, 'complete': False,
            '_unmeasured_phases': ['upload', 'submit', 'query']}


def record_transport_phase(call, remote, phase, seconds, finished_at, *, terminal=False,
                           started_at=None, status='succeeded'):
    """Append a measured HTTP phase once; callers serialize one immutable update."""
    call = deepcopy(call)
    timing = transport_timing(call, remote)
    seconds = round(max(0, seconds), 4)
    key = phase + '_seconds'
    previous = _seconds(timing.get(key))
    known_empty_queries = phase == 'query' and timing.get('query_count') == 0
    unknown = set(timing.get('_unmeasured_phases', [] if timing.get('complete') else ['upload', 'submit', 'query']))
    if known_empty_queries:
        unknown.discard('query')
    timing[key] = (None if phase in unknown else round((previous or 0) + seconds, 4))
    timing['_unmeasured_phases'] = sorted(unknown)
    timing['complete'] = not unknown
    measured = _seconds(timing.get('measured_request_seconds'))
    timing['measured_request_seconds'] = round((measured or 0) + seconds, 4)
    timing['request_seconds'] = timing['measured_request_seconds'] if timing.get('complete') else None
    if phase == 'query':
        previous_count = timing.get('query_count')
        timing['query_count'] = previous_count + 1 if isinstance(previous_count, int) else None
    # Older measurements have no request positions. Only the request actually
    # measured here gets an interval; never distribute historical totals.
    if started_at and _elapsed(started_at, finished_at) is not None:
        spans = deepcopy(timing.get('spans') or [])
        ordinal = timing.get('query_count') if phase == 'query' else None
        if not isinstance(ordinal, int):
            ordinal = 1 + sum(span.get('phase') == phase for span in spans)
        spans.append({'id': f'{phase}:{ordinal}', 'phase': phase,
                      'started_at': started_at, 'finished_at': finished_at,
                      'seconds': seconds, 'status': status})
        timing['spans'] = spans
    timing['phase'] = phase + '_finished'
    if terminal:
        timing['finished_at'] = finished_at
        timing['elapsed_seconds'] = _elapsed(timing.get('started_at'), finished_at)
        if phase == 'query' and status == 'succeeded':
            timing['result_received_at'] = finished_at
    call['transport'] = timing
    return call


def remote_call(remote, filename):
    """An accepted request proves one call, but never proves an unmeasured duration."""
    measured = json.loads(remote['recognition_call_json'] or '{}')
    if not measured and not remote['req_uuid']:
        return None
    call = deepcopy(measured) if measured else {
        'stage': 'routing', 'stage_label': '整页文字与版式',
        'method': 'danzhengtong', 'method_label': '单证通', 'action': '整页单证识别',
        'input_name': filename, 'started_at': remote['submitted_at'],
        'seconds': None, 'timing_recorded': False,
    }
    call['_remote_task_id'] = remote['id']
    call['_remote_job_id'] = remote.get('job_id', '') if isinstance(remote, dict) else remote['job_id']
    call['_remote_status'] = remote['status']
    call['status'] = ('succeeded' if remote['status'] in {'received', 'applied', 'conflict'}
                      else 'failed' if remote['status'] in {'failed', 'unknown', 'cancelled'} else 'running')
    call['recognition_invoked'] = bool(remote['req_uuid']) or call.get('recognition_invoked', True)
    call['transport'] = transport_timing(call, dict(remote))
    if call['status'] == 'succeeded':
        call['finished_at'] = (call['transport'].get('result_received_at')
                               or call['transport'].get('finished_at') or remote['last_query_at'])
    call['submission_unknown'] = (remote['status'] == 'unknown' and not remote['req_uuid']
                                  and call.get('recognition_invoked') is False
                                  and call['transport'].get('phase') == 'submitting')
    return call


def _boundary(values, *, latest=False):
    valid = []
    for value in values:
        try:
            valid.append((datetime.fromisoformat(value).timestamp(), value))
        except (TypeError, ValueError, OverflowError):
            pass
    return (max(valid) if latest else min(valid))[1] if valid else ''


def _regular_run(base, recorded):
    regular = deepcopy(base.get('_regular_timing'))
    older_remote = any(row.get('method') == 'danzhengtong' for row in base.get('calls') or [])
    if regular is None:
        regular = {
            'processing_seconds': base.get('processing_seconds') if recorded and not older_remote else None,
            'model_initialization_seconds': base.get('model_initialization_seconds') if recorded else None,
            'started_at': base.get('started_at', '') if recorded else '',
            # Old combined snapshots overwrote this with the remote end. That
            # is no longer proof of when the regular branch finished.
            'finished_at': base.get('finished_at', '') if recorded and not older_remote else '',
            'status': base.get('status', 'unknown') if recorded and not older_remote else 'unknown',
            'run_id': base.get('run_id', ''),
        }
    regular.setdefault('started_at', base.get('started_at', '') if recorded else '')
    regular.setdefault('finished_at', '')
    regular.setdefault('status', 'completed' if regular.get('processing_seconds') is not None else 'unknown')
    regular.setdefault('run_id', base.get('run_id', ''))
    if not regular['finished_at'] and _seconds(regular.get('processing_seconds')) is not None:
        try:
            regular['finished_at'] = (datetime.fromisoformat(regular['started_at'])
                                      + timedelta(seconds=float(regular['processing_seconds']))).isoformat()
            regular['finished_at_approximate'] = True
        except (TypeError, ValueError, OverflowError):
            pass
    return regular


def merge_remote_snapshot(base, call, *, incremental=False):
    call = deepcopy(call)
    physical = call.pop('_physical_calls', None)
    base = deepcopy(base) if isinstance(base, dict) else {}
    recorded = base.get('_regular_recorded', isinstance(base.get('calls'), list))
    calls = [row for row in base.get('calls') or [] if row.get('method') != 'danzhengtong']
    transports = [row for row in base.get('transport_calls') or [] if row.get('method') != 'danzhengtong']
    regular = _regular_run(base, recorded)
    if incremental:
        provenance = {'id': regular.get('run_id', ''), 'started_at': regular.get('started_at', ''),
                      'finished_at': regular.get('finished_at', ''),
                      'approximate': bool(regular.get('finished_at_approximate')), 'retained': True}
        for row in calls:
            row['_run'] = {**provenance, **row.get('_run', {}), 'retained': True}
    for invocation in physical if physical is not None else [call]:
        (calls if invocation.get('recognition_invoked', True) else transports).append(deepcopy(invocation))
    for index, row in enumerate(calls, 1):
        row['id'] = index
    transport = call.get('transport') or {}
    local_required = not incremental and call.get('_regular_required', recorded)
    start = _boundary([transport.get('started_at'), regular.get('started_at') if local_required else None])
    remote_end = transport.get('applied_at') or transport.get('result_received_at') or transport.get('finished_at')
    remote_complete = call.get('_remote_status', call['status']) not in {'received', 'queued', 'submitting', 'waiting', 'running'}
    local_complete = not local_required or (bool(regular.get('finished_at'))
                                           and regular.get('status') not in {'running', 'unknown'})
    finished = _boundary([remote_end, regular.get('finished_at') if local_required else None], latest=True)
    # A shared wall-clock range includes overlap and waiting exactly once.
    # Submission-only historical bounds are not the complete processing run.
    known = (transport.get('elapsed_scope') == 'upload_to_result' and remote_complete and local_complete
             and transport.get('started_at') and remote_end
             and (not local_required or regular.get('started_at')))
    total = _elapsed(start, finished) if known else None
    completion_scope = ('result_applied' if transport.get('applied_at') else
                        'result_received' if call['status'] == 'succeeded' else 'remote_failed')
    initialization = 0 if incremental or not local_required else regular.get('model_initialization_seconds')
    note = ('保留此前其他方法的调用明细，并补充本次单证通调用。' if incremental and recorded
            else '此前其他方法的逐次调用明细未保存，无法恢复；这里仅展示已记录的调用。' if incremental
            else '')
    if call.get('recognition_invoked') is False:
        note += ('本次单证通提交状态待确认；仅保留已记录的接口耗时。' if call.get('submission_unknown')
                 else '本次单证通尚未提交识别；上传接口耗时单独记录。')
    elif call.get('timing_recorded') is False:
        note += '这次单证通请求已受理，但未采集逐次耗时。'
    else:
        note += '单证通耗时为提交与查询请求的累计耗时，不含等待间隔。'
    return {**base, 'version': 5, '_regular_timing': regular, '_regular_recorded': recorded,
            'incremental': incremental or bool(base.get('incremental')),
            'run_id': call.get('_remote_job_id') or base.get('run_id', ''),
            'started_at': start or call.get('started_at', ''),
            'finished_at': finished if remote_complete and local_complete else '',
            'processing_seconds_approximate': bool(local_required and regular.get('finished_at_approximate')
                                                 or completion_scope == 'result_received'),
            'processing_finished_scope': completion_scope if known else '',
            'processing_seconds': total, 'model_initialization_seconds': initialization,
            'recognition_seconds': (round(max(0, total - initialization), 4)
                                    if total is not None and initialization is not None else None),
            'status': ('failed' if call['status'] == 'failed' else
                       'running' if not remote_complete or not local_complete else 'completed'),
            'summary': {'total': len(calls), 'succeeded': sum(row.get('status') == 'succeeded' for row in calls),
                        'failed': sum(row.get('status') == 'failed' for row in calls),
                        'cache_hits': (base.get('summary') or {}).get('cache_hits', 0)},
            'calls': calls, 'transport_calls': transports, 'note': note}


def combined_remote_trace(con, filename, current=None):
    """Return only the newest job's remote lane; a late older lane cannot replace it."""
    remote = con.execute('''SELECT d.*,j.filename,j.payload_json,j.attempt,
        j.result_id AS job_result_id,j.target_result_id FROM recognition_jobs j
        JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1
        WHERE j.filename=? AND j.rowid=(SELECT MAX(rowid) FROM recognition_jobs WHERE filename=?)''',
        (filename, filename)).fetchone()
    if remote is None:
        return None
    if current is not None and not current['job_id']:
        try:
            if datetime.fromisoformat(current['started_at']) > datetime.fromisoformat(remote['created_at']):
                return None  # A newer direct recognition already replaced this job's trace.
        except (TypeError, ValueError):
            pass
    call = combined_remote_call(dict(remote), filename)
    if call is None:
        return None
    options = json.loads(remote['payload_json'])
    incremental = bool(options.get('_preserve_provider_results'))
    plan = options.get('recognition_config')
    call['_regular_required'] = (not incremental and options.get('_execution_scope') != 'danzhengtong'
                                 and (not isinstance(plan, dict) or any(
                                     method != 'danzhengtong' for methods in plan.values()
                                     if isinstance(methods, list) for method in methods)))
    base = (json.loads(current['snapshot_json']) if current is not None
            and (incremental or current['job_id'] == remote['job_id']) else {})
    return remote, merge_remote_snapshot(base, call, incremental=incremental)


def combined_remote_call(remote, filename):
    """Retain each page/stamp invocation while measuring their common wall span."""
    state = json.loads(remote.get('seal_requests_json') or '{}')
    if not state:
        return remote_call(remote, filename)
    page = dict(remote)
    page_state = state.get('page') or {}
    if page_state.get('status') in {'failed', 'unknown'}:
        page['status'] = page_state['status']
    elif json.loads(page.get('result_json') or '{}'):
        page['status'] = 'received' if remote['status'] != 'applied' else 'applied'
    elif remote['status'] in {'applied', 'conflict'} and page.get('req_uuid'):
        page['status'] = remote['status']  # Legacy accepted completion may lack raw results.
    elif page_state.get('status') in {'queued', 'uploading', 'submitting', 'waiting'}:
        page['status'] = 'waiting' if page.get('req_uuid') else page_state['status']
    physical = []
    document = remote_call(page, filename)
    if document is not None:
        document['request_scope'] = 'page'
        if page_state.get('error'):
            # The task's error surface already owns provider diagnostics.
            # Physical traces retain their existing generic, redacted error.
            document.setdefault('error', '整页识别异常，详见任务错误')
        physical.append(document)
    for child in state.get('requests', []):
        measured = child.get('call')
        if not measured:
            continue
        invocation = deepcopy(measured)
        status = child.get('status')
        invocation.update(request_scope='seal', _remote_task_id=f"{remote['id']}:seal:{child['index']}",
                          _remote_job_id=remote['job_id'], _remote_status=status,
                          status='succeeded' if status in {'completed', 'received'} else
                                 'failed' if status in {'failed', 'unknown'} else 'running',
                          recognition_invoked=bool(child.get('req_uuid')) or invocation.get('recognition_invoked', False))
        invocation['submission_unknown'] = status == 'unknown' and not child.get('req_uuid')
        if child.get('error'):
            invocation['error'] = child['error']
        physical.append(invocation)
    if not physical:
        return None
    aggregate = deepcopy(physical[0])
    timings = [invocation.get('transport') or {} for invocation in physical]
    started = _boundary([timing.get('started_at') for timing in timings])
    received = _boundary([timing.get('result_received_at') or timing.get('finished_at') for timing in timings], latest=True)
    applied = _boundary([timing.get('applied_at') for timing in timings], latest=True)
    complete = all(timing.get('complete') for timing in timings)
    scope = ('upload_to_result' if all(item.get('elapsed_scope') == 'upload_to_result' for item in timings)
             else 'submission_to_result')
    timing = {'started_at': started, 'finished_at': received, 'result_received_at': received,
              'elapsed_scope': scope, 'complete': complete,
              'elapsed_seconds': _elapsed(started, received), 'applied_at': applied}
    for key in ('upload_seconds', 'submit_seconds', 'query_seconds', 'request_seconds', 'measured_request_seconds'):
        values = [_seconds(item.get(key)) for item in timings]
        timing[key] = round(sum(values), 4) if all(value is not None for value in values) else None
    timing['query_count'] = sum(item.get('query_count') or 0 for item in timings)
    aggregate.update(transport=timing, _physical_calls=physical, _remote_status=remote['status'],
                     _remote_job_id=remote['job_id'], recognition_invoked=any(item.get('recognition_invoked') for item in physical),
                     status='succeeded' if remote['status'] in {'received', 'applied', 'conflict'} else
                            'failed' if remote['status'] in {'failed', 'unknown', 'cancelled'} else 'running')
    return aggregate
