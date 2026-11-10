"""Summarize saved remote timings without changing physical-call accounting."""
from datetime import datetime
from math import isfinite


_LABELS = {'qingtong': '清瞳', 'danzhengtong': '单证通'}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        return float(value) if isfinite(value) else None
    except OverflowError:
        return None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sum(values):
    if not values or any(value is None for value in values):
        return None
    total = sum(values)
    return round(total, 4) if isfinite(total) else None


def _status(values):
    if any(value in {'running', 'queued', 'submitting', 'waiting'} for value in values):
        return 'running'
    if any(value in {'failed', 'unknown', 'cancelled', 'interrupted'} for value in values):
        return 'failed'
    if values and all(value in {'succeeded', 'completed', 'received', 'applied', 'conflict'} for value in values):
        return 'succeeded'
    return 'unknown'


def _invoked(call):
    return call.get('recognition_invoked') is not False and call.get('invoked') is not False


def _calls(snapshot):
    """A durable remote task may occur in both projections; count it once."""
    output, positions = [], {}
    for key in ('calls', 'transport_calls'):
        rows = snapshot.get(key)
        for call in rows if isinstance(rows, list) else []:
            if not isinstance(call, dict) or call.get('method') not in _LABELS:
                continue
            task = call.get('_remote_task_id')
            identity = (call['method'], str(task)) if task is not None and task != '' else None
            if identity is None or identity not in positions:
                if identity is not None:
                    positions[identity] = len(output)
                output.append(dict(call))
                continue
            index = positions[identity]
            previous = output[index]
            merged = {**previous, **{k: v for k, v in call.items() if v is not None}}
            if _invoked(previous) and not _invoked(call):
                # A retained upload-only projection cannot supersede the same
                # task's later recognition outcome.
                for key in ('status', 'seconds', 'timing_recorded'):
                    if key in previous:
                        merged[key] = previous[key]
            merged['recognition_invoked'] = _invoked(previous) or _invoked(call)
            merged.pop('invoked', None)
            merged['transport'] = {
                **(previous.get('transport') or {}),
                **{k: v for k, v in (call.get('transport') or {}).items() if v is not None},
            }
            output[index] = merged
    return output


def _request(call):
    transport = call.get('transport') or {}
    full = _number(transport.get('request_seconds'))
    if full is not None:
        return full, 'full'
    seconds = _number(call.get('seconds'))
    measured = _number(transport.get('measured_request_seconds'))
    if call['method'] == 'qingtong':
        if (seconds is not None and call.get('timing_recorded') is not False
                and call.get('status') != 'running'):
            return seconds, 'full'
    elif (call.get('_remote_task_id') not in (None, '') and call.get('timing_recorded') is True
          and transport.get('elapsed_scope') != 'upload_to_result' and seconds is not None):
        # Legacy async timing measured submit/query HTTP time, excluding upload.
        return seconds, 'submit_query'
    if measured is not None:
        return measured, 'partial'
    if seconds is not None and call.get('timing_recorded') is not False and call.get('status') != 'running':
        return seconds, 'legacy_call'
    return None, 'unknown'


def _request_total(calls):
    timings = [_request(call) for call in calls]
    known = [(seconds, scope) for seconds, scope in timings if seconds is not None]
    if not known:
        return None, 'unknown'
    scopes = {scope for _, scope in known}
    if 'legacy_call' in scopes and (len(scopes) > 1 or len(known) != len(calls)):
        return None, 'unknown'  # A legacy wall-time range cannot be summed as HTTP.
    seconds = _sum([value for value, _ in known])
    if seconds is None:
        return None, 'unknown'
    scope = next(iter(scopes)) if len(scopes) == 1 and len(known) == len(calls) else 'partial'
    return seconds, scope


def _blank(method):
    return dict(method=method, label=_LABELS[method], status='unknown', calls=None,
                request_seconds=None, request_scope='unknown', elapsed_seconds=None,
                elapsed_scope=None, elapsed_approximate=False, upload_seconds=None,
                submit_seconds=None, query_seconds=None, query_count=None,
                server_seconds=None, note='')


def _from_calls(method, calls):
    result = _blank(method)
    result.update(status=_status([call.get('status') for call in calls]),
                  calls=sum(_invoked(call) for call in calls))
    result['request_seconds'], result['request_scope'] = _request_total(calls)
    transports = [call.get('transport') or {} for call in calls]
    for key in ('upload_seconds', 'submit_seconds', 'query_seconds', 'server_seconds'):
        result[key] = _sum([_number(transport.get(key)) for transport in transports])
    counts = [_count(transport.get('query_count')) for transport in transports]
    result['query_count'] = sum(counts) if counts and all(value is not None for value in counts) else None
    scopes = {transport.get('elapsed_scope') for transport in transports}
    elapsed = _sum([_number(transport.get('elapsed_seconds')) for transport in transports])
    if elapsed is not None and len(scopes) == 1 and scopes <= {'upload_to_result', 'submission_to_result', 'request'}:
        scope = next(iter(scopes))
        if method == 'danzhengtong' and len(transports) > 1:
            try:
                starts = [datetime.fromisoformat(item['started_at']) for item in transports]
                ends = [datetime.fromisoformat(item.get('result_received_at') or item['finished_at'])
                        for item in transports]
                if all(end >= start for start, end in zip(starts, ends)):
                    elapsed = round((max(ends) - min(starts)).total_seconds(), 4)
            except (KeyError, TypeError, ValueError):
                # Old totals have no positions; do not invent overlap.
                pass
        result.update(elapsed_seconds=elapsed, elapsed_scope=scope,
                      elapsed_approximate=any(transport.get('elapsed_approximate', scope == 'submission_to_result')
                                              is True for transport in transports))
    elif method == 'qingtong' and result['request_scope'] == 'full':
        result.update(elapsed_seconds=result['request_seconds'], elapsed_scope='request')
    return result


def _api_candidates(evidence):
    checks = []
    for variant in (evidence.get('recognition_variants') or {}).get('seal') or []:
        if isinstance(variant, dict):
            check = (variant.get('details') or {}).get('seal_check')
            if isinstance(check, dict):
                checks.append(check)
    if isinstance(evidence.get('seal_check'), dict):
        checks.append(evidence['seal_check'])
    for check in checks:
        for source in (check.get('qingtong_channel') or {}, check, check.get('local_channel') or {}):
            api = source.get('api') if isinstance(source, dict) else None
            if not isinstance(api, dict) or api.get('enabled') is False or api.get('requested') is False:
                continue
            timing = api.get('timing') or {}
            if (_number(timing.get('request_seconds')) is not None or api.get('ok') is True
                    or api.get('response') or _number(api.get('http_status')) is not None):
                yield api


def _qingtong_evidence(evidence):
    apis = list(_api_candidates(evidence))
    if not apis:
        return None
    # Copies in aggregate/variant channels describe the same response, not more
    # requests. Prefer an explicit measured copy and select just one.
    return max(apis, key=lambda api: (
        _number((api.get('timing') or {}).get('request_seconds')) is not None,
        _number((api.get('timing') or {}).get('server_seconds')) is not None,
        _number((api.get('response') or {}).get('processing_time')) is not None
        if isinstance(api.get('response'), dict) else False,
    ))


def _notes(result, *, submission_unknown=False):
    scope = result['request_scope']
    notes = {
        'full': '请求耗时含上传及响应下载。',
        'submit_query': '仅提交与查询请求累计耗时，未记录上传耗时。',
        'legacy_call': ('历史清瞳调用耗时含响应处理，并非精确HTTP计时。'
                        if result['method'] == 'qingtong' else '历史调用计时可能含等待，无法还原HTTP耗时。'),
        'partial': '仅累计已记录的请求耗时，部分请求计时缺失。',
        'unknown': '未记录可确认的请求耗时。',
    }
    note = notes[scope]
    if result['elapsed_scope'] == 'submission_to_result':
        note += '历时按受理至取回时间估算，包含等待。'
    elif result['elapsed_scope'] == 'upload_to_result':
        note += '历时从上传至取回结果，包含等待。'
    if result['server_seconds'] is not None:
        note += '服务端耗时来自返回值，不代表HTTP请求耗时。'
    if result['calls'] == 0:
        note += ('提交状态待确认；无法确认的识别未计入次数。' if submission_unknown
                 else '尚未提交识别，接口记录不计入识别调用次数。')
    return note


def remote_interface_timings(snapshot, evidence=None):
    """Return at most one safe, explicitly scoped timing row per remote service."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    calls = _calls(snapshot)
    result = []
    for method in _LABELS:
        selected = [call for call in calls if call['method'] == method]
        api = _qingtong_evidence(evidence) if method == 'qingtong' else None
        if not selected and api is None:
            continue
        row = _from_calls(method, selected) if selected else _blank(method)
        if api is not None:
            timing = api.get('timing') or {}
            server = _number(timing.get('server_seconds'))
            payload = api.get('response')
            if server is None and isinstance(payload, dict):
                server = _number(payload.get('processing_time'))
            row['server_seconds'] = server
            if not selected:
                row['status'] = ('succeeded' if api.get('ok') is True else
                                 'failed' if api.get('ok') is False else 'unknown')
                request = _number(timing.get('request_seconds'))
                if request is not None:
                    row.update(request_seconds=request, request_scope='full',
                               elapsed_seconds=request, elapsed_scope='request')
                else:
                    steps = (evidence.get('processing_timings') or {}).get('steps') or {}
                    legacy = _number((steps.get('seal_api') or {}).get('seconds'))
                    if legacy is not None:
                        row.update(request_seconds=legacy, request_scope='legacy_call')
        row['note'] = _notes(row, submission_unknown=any(call.get('submission_unknown') is True for call in selected))
        result.append(row)
    return result
