"""Read-only time-axis projection; cumulative work is never a run duration."""
from datetime import datetime, timedelta, timezone
from math import isfinite


_TERMINAL = {'completed', 'succeeded', 'failed', 'received', 'applied', 'conflict', 'interrupted', 'incomplete'}
_LANES = {'routing': ('local_page', '本地整页'), 'fields': ('local_page', '本地整页'),
          'products': ('local_page', '本地整页'), 'date': ('date', '签收日期'),
          'seal': ('seal', '本地印章'), 'handwriting': ('handwriting', '手写签名')}


def _number(value):
    try:
        return float(value) if type(value) in (int, float) and isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _iso(value):
    return value.isoformat(timespec='microseconds') if value is not None else ''


def _duration(start, end):
    return round((end - start).total_seconds(), 4) if start and end and end >= start else None


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _remote_lane(call):
    """Use recorded request identity; an old seal stage may have read a page."""
    scope = call.get('request_scope')
    if scope not in ('page', 'seal'):
        action = call.get('action') if isinstance(call.get('action'), str) else ''
        page, seal = '整页' in action, '印章' in action
        scope = 'page' if page and not seal else 'seal' if seal and not page else ''
    return {'page': ('danzhengtong_page', '单证通整页', 'page'),
            'seal': ('danzhengtong_seal', '单证通印章', 'seal')}.get(
                scope, ('danzhengtong', '单证通（未分类）', ''))


def _durable_remote(call):
    return call.get('method') == 'danzhengtong' and (
        call.get('_remote_task_id') not in (None, '')
        or isinstance(call.get('transport'), dict) and bool(call['transport']))


def _seal_caption(call, scope):
    if scope != 'seal':
        return ''
    index = call.get('seal_index')
    if type(index) is not int or index < 0:
        identity = str(call.get('_remote_task_id') or '')
        suffix = identity.rsplit(':seal:', 1)
        index = int(suffix[1]) if len(suffix) == 2 and suffix[1].isdigit() else None
    return f'第{index + 1}枚 · ' if index is not None else ''


def _span(identity, label, lane, lane_label, start, end, *, kind, status, approximate=False, **extra):
    left, right = _time(start), _time(end)
    if left is None or right is None or right < left:
        return None
    return dict(id=str(identity), label=str(label or '识别'), lane=lane, lane_label=lane_label,
                started_at=_iso(left), finished_at=_iso(right), seconds=_duration(left, right),
                kind=kind, status=status, approximate=bool(approximate), **extra)


def _regular_metadata(snapshot, remote):
    saved = snapshot.get('_regular_timing')
    saved = saved if isinstance(saved, dict) else {}
    start = saved.get('started_at') or snapshot.get('started_at')
    end = saved.get('finished_at') or (snapshot.get('finished_at') if not remote else '')
    approximate = bool(saved.get('finished_at_approximate') or saved.get('approximate'))
    seconds = _number(saved.get('processing_seconds') if remote else snapshot.get('processing_seconds'))
    # Old async snapshots overwrote their local finish with the remote finish.
    # The separately retained local wall time can recover an approximate bound.
    if not _time(end) and _time(start) and seconds is not None:
        try:
            end = _iso(_time(start) + timedelta(seconds=seconds))
            approximate = True
        except OverflowError:
            pass
    return dict(id=saved.get('run_id') or snapshot.get('run_id') or 'regular:' + str(start or 'unknown'),
                started_at=start or '', finished_at=end or '', approximate=approximate,
                status=saved.get('status') or ('completed' if _time(end) else snapshot.get('status', 'unknown')))


def _call_span(call, index):
    start, end = call.get('started_at'), call.get('finished_at')
    approximate = False
    seconds = _number(call.get('seconds'))
    remote = call.get('method') == 'danzhengtong'
    # Logical async seconds can be HTTP sums. Without transport boundaries,
    # only the call's two actual timestamps locate a remote interval.
    if remote and (not _time(start) or not _time(end)):
        return None
    if not _time(end) and _time(start) and seconds is not None and call.get('status') in _TERMINAL:
        try:
            end = _iso(_time(start) + timedelta(seconds=seconds))
            approximate = True
        except OverflowError:
            return None
    if remote:
        lane, label, scope = _remote_lane(call)
    else:
        lane, label = (('qingtong', '清瞳') if call.get('method') == 'qingtong'
                       else _LANES.get(call.get('stage'), ('other', '其他识别')))
        scope = ''
    action = str(call.get('action') or call.get('method_label') or '识别')
    if remote and scope == 'seal':
        caption = _seal_caption(call, scope)
        action = (caption + action if caption else
                  action + ' · ' + str(call['input_name']) if call.get('input_name') else action)
    return _span('call:' + str(call.get('id', index)), action,
                 lane, label, start, end, kind='recognition', status=call.get('status', 'unknown'),
                 approximate=approximate, call_id=call.get('id', index), method=str(call.get('method') or ''),
                 **({'request_scope': scope} if remote else {}))


def _remote_spans(call, index, observed):
    timing = call.get('transport') or {}
    timing = timing if isinstance(timing, dict) else {}
    identity = str(call.get('_remote_task_id') or index)
    lane, lane_label, scope = _remote_lane(call)
    caption = _seal_caption(call, scope)
    evidence = dict(call_id=call.get('id'), method='danzhengtong', request_scope=scope,
                    remote_task_id=call.get('_remote_task_id'))
    start = timing.get('started_at')
    end = timing.get('result_received_at') or timing.get('finished_at')
    ongoing = call.get('status') == 'running' and not _time(end)
    if ongoing and _time(start) and observed >= _time(start):
        end = _iso(observed)
    lifecycle = _span('remote:' + identity, lane_label + ' · ' + caption + (
        '上传至终止' if call.get('status') == 'failed' else
        '上传至结果' if timing.get('elapsed_scope') == 'upload_to_result' else '已记录区间'),
        lane, lane_label, start, end, kind='lifecycle', status=call.get('status', 'unknown'),
        approximate=timing.get('elapsed_scope') != 'upload_to_result', ongoing=ongoing,
        request_seconds=_number(timing.get('request_seconds')), **evidence)
    output = [lifecycle] if lifecycle else []
    phase_names = {'upload': '上传', 'submit': '提交识别', 'query': '查询结果'}
    phases = []
    for number, phase in enumerate(_rows(timing.get('spans')), 1):
        kind = phase.get('phase')
        if kind not in phase_names:
            continue
        value = _span('remote:' + identity + ':' + str(phase.get('id') or number), caption + phase_names[kind],
            lane, lane_label, phase.get('started_at'), phase.get('finished_at'),
            kind='request', status=phase.get('status', 'succeeded'), parent_id='remote:' + identity,
            phase=kind, **evidence)
        if value and lifecycle and not (
                _time(lifecycle['started_at']) <= _time(value['started_at'])
                <= _time(value['finished_at']) <= _time(lifecycle['finished_at'])):
            continue
        if value:
            output.append(value)
            phases.append(value)
    # A known cumulative HTTP total does not locate old individual requests.
    # Only a complete matching set of spans makes its complement observable.
    counts = {phase: sum(row['phase'] == phase for row in phases) for phase in phase_names}
    complete = (timing.get('complete') is True and lifecycle and not ongoing
                and counts['upload'] == counts['submit'] == 1
                and type(timing.get('query_count')) is int and counts['query'] == timing['query_count'])
    for phase in phase_names:
        actual = sum(row['seconds'] for row in phases if row['phase'] == phase)
        expected = _number(timing.get(phase + '_seconds'))
        complete = complete and expected is not None and abs(actual - expected) <= .05
    ordered_phases = sorted(phases, key=lambda row: row['started_at'])
    complete = complete and all(_time(previous['finished_at']) <= _time(following['started_at'])
                                for previous, following in zip(ordered_phases, ordered_phases[1:]))
    if complete:
        left, right = _time(start), _time(end)
        cursor = left
        if all(left <= _time(row['started_at']) <= _time(row['finished_at']) <= right for row in phases):
            for number, row in enumerate(ordered_phases, 1):
                begin, finish = _time(row['started_at']), _time(row['finished_at'])
                if begin > cursor:
                    output.append(_span(f'remote:{identity}:wait:{number}', caption + '等待 / 调度间隔',
                        lane, lane_label, _iso(cursor), _iso(begin), kind='wait', status='succeeded',
                        parent_id='remote:' + identity, **evidence))
                cursor = max(cursor, finish)
            if cursor < right:
                output.append(_span(f'remote:{identity}:wait:end', caption + '等待 / 调度间隔', lane,
                    lane_label, _iso(cursor), _iso(right), kind='wait', status='succeeded',
                    parent_id='remote:' + identity, **evidence))
    applied = timing.get('applied_at') or call.get('applied_at')
    received = timing.get('result_received_at') or timing.get('finished_at')
    apply_started = timing.get('apply_started_at')
    if _time(apply_started):
        waiting = _span('remote:' + identity + ':wait:apply', caption + '等待其他分支 / 等待保存',
            lane, lane_label, received, apply_started, kind='wait', status='succeeded',
            parent_id='remote:' + identity, **evidence)
        if waiting:
            output.append(waiting)
    finalizing = _span('remote:' + identity + ':apply', caption + (
        '结果合并与保存' if _time(apply_started) else '返回后等待与保存（未细分）'), lane, lane_label,
        apply_started or received, applied, kind='finalizing', status='succeeded',
        approximate=not bool(_time(apply_started)), parent_id='remote:' + identity, **evidence)
    if finalizing:
        output.append(finalizing)
    download = timing.get('image_download')
    if isinstance(download, dict) and download.get('status') in {'running', 'completed', 'failed'}:
        downloading = download.get('status') == 'running' and not _time(download.get('finished_at'))
        image = _span('remote:' + identity + ':image', caption + '旋正图下载', lane, lane_label,
            download.get('started_at'), _iso(observed) if downloading else download.get('finished_at'),
            kind='request', phase='download', status='running' if downloading else
            'succeeded' if download.get('status') == 'completed' else 'failed', ongoing=downloading,
            parent_id='remote:' + identity, **evidence)
        if image:
            output.append(image)
    return output, timing


def _run(metadata, spans, *, kind, label, known=False, elapsed_label='全流程历时', note=''):
    starts = [_time(span['started_at']) for span in spans]
    ends = [_time(span['finished_at']) for span in spans]
    start = _time(metadata.get('started_at'))
    end = _time(metadata.get('finished_at'))
    measured_start, measured_end = start, end
    if starts:
        start = min([start] + starts) if start else min(starts)
    if ends:
        end = max([end] + ends) if end else max(ends)
    status = metadata.get('status', 'unknown')
    elapsed = _duration(measured_start, measured_end) if known and status in _TERMINAL else None
    return dict(id=str(metadata['id']), label=label, kind=kind, started_at=_iso(start), finished_at=_iso(end),
                elapsed_seconds=elapsed,
                approximate=bool(metadata.get('approximate')), complete=elapsed is not None,
                elapsed_label=elapsed_label, status=status, spans=spans, note=note)


def recognition_timeline(snapshot, *, run_key='', status=None, now=None):
    """Build separately dated runs for retained evidence and current execution."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    calls = _rows(snapshot.get('calls'))
    remote, seen = [], set()
    for call in calls + _rows(snapshot.get('transport_calls')):
        if not _durable_remote(call):
            continue
        identity = str(call.get('_remote_task_id') or id(call))
        if identity not in seen:
            remote.append(call)
            seen.add(identity)
    regular = _regular_metadata(snapshot, bool(remote))
    incremental = bool(snapshot.get('incremental'))
    observed = now or datetime.now(timezone.utc)
    current_spans, retained = [], {}

    def add_regular(span, provenance=None, run_id=None):
        if span is None:
            return
        meta = provenance if isinstance(provenance, dict) else regular
        retained_call = incremental or meta.get('retained') is True
        if retained_call:
            saved_id = (meta.get('id') or meta.get('run_id')) if isinstance(provenance, dict) else None
            identity = str(saved_id or run_id or regular['id'])
            if identity not in retained:
                retained[identity] = {'meta': {**regular, **meta, 'id': identity}, 'spans': []}
            retained[identity]['spans'].append(span)
        else:
            current_spans.append(span)

    for index, call in enumerate(calls, 1):
        if not _durable_remote(call):
            add_regular(_call_span(call, index), call.get('_run'), call.get('run_id'))
    image = snapshot.get('image_processing') or {}
    for index, row in enumerate(_rows(image.get('spans') if isinstance(image, dict) else None), 1):
        add_regular(_span('image:' + str(index), row.get('label'), 'image', '图像处理',
            row.get('started_at'), row.get('finished_at'), kind='image', status=row.get('status', 'succeeded'),
            operation=row.get('operation', ''), stage=row.get('stage', '')), row.get('_run'), row.get('run_id'))
    for index, row in enumerate(_rows(snapshot.get('model_initialization_spans')), 1):
        add_regular(_span('initialization:' + str(index), '模型初始化', 'initialization', '模型初始化',
            row.get('started_at'), row.get('finished_at'), kind='initialization',
            status=row.get('status', 'unknown')), row.get('_run'), row.get('run_id'))

    overall = status or snapshot.get('status') or 'unknown'
    note, approximate, known = '', False, False
    elapsed_label = '全流程历时'
    starts, ends = [], []
    regular_recorded = snapshot.get('_regular_recorded', isinstance(snapshot.get('calls'), list))
    if not incremental and regular_recorded:
        if _time(regular['started_at']):
            starts.append(_time(regular['started_at']))
        if _time(regular['finished_at']):
            ends.append(_time(regular['finished_at']))
        approximate = regular['approximate']
        known = (_duration(_time(regular['started_at']), _time(regular['finished_at'])) is not None
                 and regular.get('status') in _TERMINAL)
    boundaries = []
    for index, call in enumerate(remote, 1):
        spans, timing = _remote_spans(call, index, observed)
        if not spans:
            recorded = _call_span(call, index)
            spans = [recorded] if recorded else []
        current_spans.extend(spans)
        start = _time(timing.get('started_at'))
        received = _time(timing.get('result_received_at') or timing.get('finished_at'))
        applied = _time(timing.get('applied_at') or call.get('applied_at'))
        finish = applied or received
        if start:
            starts.append(start)
        if finish:
            ends.append(finish)
        full = timing.get('elapsed_scope') == 'upload_to_result' and start is not None
        remote_state = call.get('_remote_status', call.get('status'))
        remote_complete = remote_state not in {'queued', 'submitting', 'waiting', 'running', 'received'}
        boundaries.append(bool(full and finish and remote_complete))
        if not applied and call.get('status') == 'failed':
            elapsed_label = '截至运行终止'
        elif not applied and remote_complete:
            approximate = True
            elapsed_label = '截至结果取回'
        if not full:
            note = '历史记录缺少上传开始时间，仅展示已记录的远程区间，完整总历时未知。'
        elif remote_complete and not timing.get('spans'):
            note = '历史记录未保存上传、提交及各次查询的起止时间，内部区间不作推测。'
    if remote:
        regular_required = not incremental and any(call.get('_regular_required', bool(snapshot.get('_regular_recorded') or any(not _durable_remote(row) for row in calls))) for call in remote)
        known = all(boundaries) and (not regular_required or known)
        if not incremental and not regular_required and not current_spans:
            starts, ends = [], []
        if elapsed_label == '截至结果取回' and not note:
            note = '历时截至结果取回；历史记录未单独采集最后的结果整理与保存时间。'
    meta = dict(id=run_key or snapshot.get('run_id') or 'current', status=overall,
                started_at=_iso(min(starts)) if starts else '', finished_at=_iso(max(ends)) if ends else '',
                approximate=approximate)
    current = _run(meta, current_spans, kind='current', label='本次运行', known=known,
                   elapsed_label=elapsed_label, note=note)
    runs = [current] if current_spans or starts else []
    for item in retained.values():
        metadata = item['meta']
        runs.append(_run(metadata, item['spans'], kind='retained', label='此前保留的识别',
            known=bool(_time(metadata.get('started_at')) and _time(metadata.get('finished_at'))),
            note='这些方法在此前运行中执行，本次复用其识别结果。'))
    return dict(version=1, runs=runs, elapsed_seconds=current['elapsed_seconds'],
                approximate=current['approximate'], elapsed_label=current['elapsed_label'],
                started_at=current['started_at'], finished_at=current['finished_at'], note=current['note'])
