from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from receipt_ocr.persistence.run_timeline import recognition_timeline
from test_job_queue import store
from test_receipt_runs import save


ORIGIN = datetime(2026, 11, 4, 5, 35, 3, tzinfo=timezone.utc)


def stamp(seconds):
    return (ORIGIN + timedelta(seconds=seconds)).isoformat(timespec='microseconds')


def local_call(index, start, seconds, stage='routing', method='paddle_v6', **extra):
    return dict(id=index, stage=stage, method=method, method_label=method, action='识别',
                started_at=stamp(start), finished_at=stamp(start + seconds), seconds=seconds,
                status='succeeded', run_id='local-run', **extra)


def remote_call(*, start=0, end=13.42, applied=14, status='applied', phase_spans=None):
    timing = dict(started_at=stamp(start), finished_at=stamp(end), result_received_at=stamp(end),
        elapsed_seconds=end-start, elapsed_scope='upload_to_result', complete=True,
        upload_seconds=.45, submit_seconds=1.52, query_seconds=5.63, query_count=2,
        request_seconds=7.6)
    if applied is not None:
        timing['applied_at'] = stamp(applied)
    if phase_spans is not None:
        timing['spans'] = phase_spans
    return dict(id=10, method='danzhengtong', stage='routing', seconds=7.15,
        started_at=stamp(1), status='running' if status in {'waiting', 'queued'} else 'succeeded',
        _remote_status=status, _remote_task_id='remote-1', _remote_job_id='new-run',
        _regular_required=True, transport=timing)


def trace(remote=None):
    calls = [local_call(1, .67, 2.52), local_call(2, 3.76, .98, 'seal', 'qingtong'),
             local_call(3, 4.16, .17, 'date')]
    if remote is not None:
        calls.append(remote)
    return dict(run_id='local-run', started_at=stamp(0), finished_at=stamp(14 if remote else 5.77),
                status='completed', processing_seconds=12.92 if remote else 5.77,
                _regular_recorded=True,
                _regular_timing=dict(run_id='local-run', started_at=stamp(0), finished_at=stamp(5.77),
                                     status='completed', processing_seconds=5.77),
                calls=calls)


def current(timeline):
    return next(run for run in timeline['runs'] if run['kind'] == 'current')


def test_parallel_total_is_shared_wall_time_and_original_calls_stay_intact():
    snapshot = trace(remote_call())
    before = deepcopy(snapshot)
    value = recognition_timeline(snapshot)
    assert value['elapsed_seconds'] == 14
    assert value['elapsed_label'] == '全流程历时'
    assert not value['approximate']
    spans = current(value)['spans']
    qt = next(row for row in spans if row['lane'] == 'qingtong')
    date = next(row for row in spans if row['lane'] == 'date')
    assert qt['started_at'] < date['started_at'] < qt['finished_at']
    assert any(row['kind'] == 'finalizing' and row['seconds'] == .58 for row in spans)
    assert snapshot == before


def test_wait_save_and_background_download_have_separate_recorded_intervals():
    remote = remote_call()
    remote['request_scope'] = 'seal'
    remote['transport'].update(apply_started_at=stamp(13.8), image_download={
        'status': 'completed', 'started_at': stamp(14.1), 'finished_at': stamp(19.1)})
    value = recognition_timeline(trace(remote))
    spans = current(value)['spans']
    waiting = next(row for row in spans if row['id'].endswith(':wait:apply'))
    saving = next(row for row in spans if row['kind'] == 'finalizing')
    downloading = next(row for row in spans if row.get('phase') == 'download')
    assert waiting['seconds'] == .38
    assert saving['seconds'] == .2 and saving['label'] == '结果合并与保存'
    assert not saving['approximate']
    assert downloading['seconds'] == 5 and downloading['label'] == '旋正图下载'
    assert downloading['started_at'] > saving['finished_at']
    assert value['elapsed_seconds'] == 14  # Background images do not delay text completion.


def test_old_post_result_interval_is_explicitly_unsplit():
    spans = current(recognition_timeline(trace(remote_call())))['spans']
    saving = next(row for row in spans if row['kind'] == 'finalizing')
    assert saving['label'] == '返回后等待与保存（未细分）'
    assert saving['approximate'] is True


def test_actual_screenshot_legacy_timing_is_to_received_and_never_sum_of_parallel_branches():
    remote = remote_call(end=13.4262, applied=None)
    snapshot = trace(remote)
    snapshot['_regular_timing'] = dict(processing_seconds=5.7742, model_initialization_seconds=.0001)
    snapshot['processing_seconds'] = 12.9211
    for call in snapshot['calls'][:-1]:
        call.pop('finished_at')
    value = recognition_timeline(snapshot)
    assert value['elapsed_seconds'] == 13.4262
    assert value['approximate'] and value['elapsed_label'] == '截至结果取回'
    assert not any(row['kind'] in {'request', 'wait'} for row in current(value)['spans'])
    assert '起止时间' in value['note']


def test_incremental_old_evidence_has_separate_time_axis_and_does_not_extend_current_total():
    remote = remote_call(start=3600, end=3613.42, applied=3614)
    snapshot = trace(remote)
    snapshot['incremental'] = True
    value = recognition_timeline(snapshot, run_key='new-run')
    assert value['elapsed_seconds'] == 14
    assert len(value['runs']) == 2
    active, retained = value['runs']
    assert active['kind'] == 'current' and active['started_at'] == stamp(3600)
    assert all(row['lane'] == 'danzhengtong' for row in active['spans'])
    assert retained['kind'] == 'retained' and retained['started_at'] == stamp(0)
    assert retained['elapsed_seconds'] == 5.77


def phases():
    return [dict(id='upload:1', phase='upload', started_at=stamp(0), finished_at=stamp(.45), seconds=.45),
            dict(id='submit:1', phase='submit', started_at=stamp(.7), finished_at=stamp(2.22), seconds=1.52),
            dict(id='query:1', phase='query', started_at=stamp(4.22), finished_at=stamp(5.85), seconds=1.63),
            dict(id='query:2', phase='query', started_at=stamp(9.42), finished_at=stamp(13.42), seconds=4)]


def test_wait_intervals_are_only_derived_from_complete_matching_measured_http_spans():
    value = recognition_timeline(trace(remote_call(phase_spans=phases())))
    rows = current(value)['spans']
    assert sum(row['seconds'] for row in rows if row['kind'] == 'request') == pytest.approx(7.6)
    waits = [row for row in rows if row['kind'] == 'wait']
    assert sum(row['seconds'] for row in waits) == pytest.approx(5.82)
    assert [(row['started_at'], row['finished_at']) for row in waits] == [
        (stamp(.45), stamp(.7)), (stamp(2.22), stamp(4.22)), (stamp(5.85), stamp(9.42))]


@pytest.mark.parametrize('change', ['missing', 'mismatch', 'outside', 'overlap'])
def test_partial_or_inconsistent_phase_history_does_not_invent_waits(change):
    spans = phases()
    if change == 'missing':
        spans.pop()
    elif change == 'mismatch':
        spans[-1]['finished_at'] = stamp(13)
    elif change == 'outside':
        spans[-1]['started_at'], spans[-1]['finished_at'] = stamp(19), stamp(23)
    else:
        # Phases of one remote request cannot run concurrently.
        spans[-1]['started_at'], spans[-1]['finished_at'] = stamp(4.5), stamp(8.5)
    value = recognition_timeline(trace(remote_call(phase_spans=spans)))
    assert not any(row['kind'] == 'wait' for row in current(value)['spans'])


def test_submission_only_legacy_range_cannot_be_claimed_as_full_total():
    remote = remote_call(applied=None)
    remote['transport']['elapsed_scope'] = 'submission_to_result'
    value = recognition_timeline(trace(remote))
    assert value['elapsed_seconds'] is None
    assert any(row['kind'] == 'lifecycle' for row in current(value)['spans'])
    assert '上传开始' in value['note']


def test_received_before_apply_is_not_a_finished_run():
    value = recognition_timeline(trace(remote_call(applied=None, status='received')))
    assert value['elapsed_seconds'] is None
    assert not current(value)['complete']
    assert value['elapsed_label'] == '全流程历时'


def test_image_and_initialization_intervals_survive_without_changing_summary_or_call_counts():
    snapshot = trace()
    snapshot['image_processing'] = dict(seconds=.2, spans=[dict(label='裁剪', operation='crop', stage='date',
        started_at=stamp(3), finished_at=stamp(3.2), seconds=.2)])
    snapshot['model_initialization_spans'] = [dict(started_at=stamp(.1), finished_at=stamp(.2), seconds=.1)]
    value = recognition_timeline(snapshot)
    assert {'recognition', 'image', 'initialization'} <= {row['kind'] for row in current(value)['spans']}
    assert len(snapshot['calls']) == 3
    assert value['elapsed_seconds'] == 5.77


def test_zero_calls_still_has_a_measured_run_duration():
    value = recognition_timeline(dict(calls=[], started_at=stamp(0), finished_at=stamp(2),
                                     processing_seconds=2, status='completed'))
    assert value['elapsed_seconds'] == 2


def test_invalid_timestamps_and_nonfinite_seconds_do_not_make_spans_or_negative_totals():
    snapshot = dict(calls=[dict(method='paddle_v6', started_at='wrong', seconds=float('inf'), status='succeeded')],
                    started_at=stamp(2), finished_at=stamp(0), status='completed')
    value = recognition_timeline(snapshot)
    assert value['elapsed_seconds'] is None
    assert not current(value)['spans']


def test_projection_is_available_on_saved_receipt_api_without_rewriting_legacy_trace(store):
    snapshot = trace(remote_call(applied=None))
    identity = save(store.database, snapshot=snapshot)
    before = store.database.get_result(identity)
    result = store.database.latest_recognition_calls(identity)
    assert result['timeline']['elapsed_seconds'] == 13.42
    assert result['calls'] == snapshot['calls']
    assert store.database.get_result(identity) == before


def test_failed_upload_is_an_end_boundary_not_a_received_result():
    remote = remote_call(end=2, applied=None, status='failed')
    remote['status'] = 'failed'
    remote['transport'].pop('result_received_at')
    remote['_regular_required'] = False
    snapshot = dict(status='failed', calls=[], transport_calls=[remote], _regular_recorded=False)
    value = recognition_timeline(snapshot)
    assert value['elapsed_seconds'] == 2 and value['elapsed_label'] == '截至运行终止'
    assert not value['approximate']
    assert '结果' not in current(value)['spans'][0]['label']


def test_incremental_call_and_images_use_the_same_original_run_identity():
    snapshot = trace(remote_call(start=3600, end=3613, applied=3614))
    snapshot['incremental'] = True
    snapshot['run_id'] = 'current-remote'
    snapshot['_regular_timing']['run_id'] = ''
    for call in snapshot['calls'][:-1]:
        call['_run'] = dict(id='', started_at=stamp(0), finished_at=stamp(5.77), retained=True)
    snapshot['image_processing'] = dict(spans=[dict(run_id='local-run', label='裁剪',
        started_at=stamp(3), finished_at=stamp(3.2))])
    value = recognition_timeline(snapshot)
    assert len([run for run in value['runs'] if run['kind'] == 'retained']) == 1


def test_failed_initialization_is_not_invented_as_success_from_its_duration():
    snapshot = dict(calls=[], started_at=stamp(0), finished_at=stamp(2), status='failed',
        model_initialization_spans=[dict(started_at=stamp(0), finished_at=stamp(2), seconds=2)])
    value = recognition_timeline(snapshot)
    initialization = next(span for span in current(value)['spans'] if span['kind'] == 'initialization')
    assert initialization['status'] == 'unknown'


def scoped_remote(scope, task_id, call_id, offset=0):
    intervals = []
    for phase in phases():
        phase = deepcopy(phase)
        for field in ('started_at', 'finished_at'):
            phase[field] = (datetime.fromisoformat(phase[field]) + timedelta(seconds=offset)).isoformat(timespec='microseconds')
        intervals.append(phase)
    value = remote_call(start=offset, end=13.42 + offset, applied=14 + offset, phase_spans=intervals)
    value.update(request_scope=scope, _remote_task_id=task_id, id=call_id,
                 action='整页单证识别' if scope == 'page' else '印章识别')
    return value


def test_page_and_multiple_stamps_keep_distinct_lanes_and_true_overlapping_intervals():
    page = scoped_remote('page', 'remote-page', 10)
    first = scoped_remote('seal', 'remote:seal:0', 11, .5)
    second = scoped_remote('seal', 'remote:seal:1', 12, 1)
    snapshot = trace(page)
    snapshot['calls'].extend([first, second])
    before = deepcopy(snapshot)
    value = recognition_timeline(snapshot)
    assert value['elapsed_seconds'] == 15
    spans = current(value)['spans']
    remote = [span for span in spans if span.get('method') == 'danzhengtong']
    assert {span['lane'] for span in remote} == {'danzhengtong_page', 'danzhengtong_seal'}
    assert len({span['id'] for span in remote}) == len(remote)
    lifecycle = [span for span in remote if span['kind'] == 'lifecycle']
    assert [(span['started_at'], span['finished_at']) for span in lifecycle] == [
        (stamp(0), stamp(13.42)), (stamp(.5), stamp(13.92)), (stamp(1), stamp(14.42))]
    assert ['第1枚' in lifecycle[1]['label'], '第2枚' in lifecycle[2]['label']] == [True, True]
    for call_id, lane in [(10, 'danzhengtong_page'), (11, 'danzhengtong_seal'), (12, 'danzhengtong_seal')]:
        own = [span for span in remote if span.get('call_id') == call_id]
        assert {span['lane'] for span in own} == {lane}
        assert sum(span['seconds'] for span in own if span['kind'] == 'request') == pytest.approx(7.6)
        assert sum(span['seconds'] for span in own if span['kind'] == 'wait') == pytest.approx(5.82)
    assert snapshot == before


@pytest.mark.parametrize('scope,action,stage,expected', [
    ('page', '印章识别', 'seal', 'danzhengtong_page'),
    ('seal', '整页单证识别', 'routing', 'danzhengtong_seal'),
    (None, '整页单证识别', 'seal', 'danzhengtong_page'),
    (None, '印章专用场景识别', 'routing', 'danzhengtong_seal'),
    (None, '识别', 'seal', 'danzhengtong'),
    (None, '', 'routing', 'danzhengtong'),
    (None, '整页及印章识别', 'seal', 'danzhengtong'),
    ([], '识别', 'seal', 'danzhengtong'),
    ({'invalid': 'scope'}, '识别', 'routing', 'danzhengtong'),
])
def test_remote_scope_wins_and_ambiguous_history_is_not_guessed_from_stage(scope, action, stage, expected):
    remote = remote_call()
    remote.update(request_scope=scope, action=action, stage=stage)
    spans = current(recognition_timeline(trace(remote)))['spans']
    actual = [span for span in spans if span.get('method') == 'danzhengtong']
    assert actual and {span['lane'] for span in actual} == {expected}
    if expected == 'danzhengtong':
        assert all(span['lane_label'] == '单证通（未分类）' for span in actual)


def test_synchronous_page_and_seal_calls_without_transport_keep_only_their_actual_recorded_bounds():
    snapshot = dict(status='completed', started_at=stamp(0), finished_at=stamp(8), processing_seconds=8,
        calls=[local_call(1, 1, 4, 'fields', 'danzhengtong', request_scope='page'),
               local_call(2, 2, 5, 'seal', 'danzhengtong', request_scope='seal', input_name='dzt-seal-0-input.png')])
    # Async HTTP totals must not replace the real wall-clock endpoints.
    snapshot['calls'][0]['seconds'] = 1
    value = recognition_timeline(snapshot)
    spans = current(value)['spans']
    assert [(span['lane'], span['started_at'], span['finished_at'], span['seconds']) for span in spans] == [
        ('danzhengtong_page', stamp(1), stamp(5), 4), ('danzhengtong_seal', stamp(2), stamp(7), 5)]
    assert all(span['kind'] == 'recognition' for span in spans)
    assert not any(span['approximate'] for span in spans)
    assert spans[1]['label'].endswith('dzt-seal-0-input.png')
    assert value['elapsed_seconds'] == 8
    assert value['elapsed_label'] == '全流程历时'


def test_remote_http_sum_without_a_recorded_end_does_not_fabricate_a_sync_interval():
    snapshot = dict(calls=[dict(method='danzhengtong', action='印章识别',
                    started_at=stamp(0), seconds=99, status='succeeded')], status='completed')
    value = recognition_timeline(snapshot)
    assert not any(span for run in value['runs'] for span in run['spans'])
    assert value['elapsed_seconds'] is None


def test_failed_seal_upload_is_visible_in_its_own_lane_without_claiming_recognition():
    call = scoped_remote('seal', 'remote:seal:0', 1)
    call.update(recognition_invoked=False, status='failed', _remote_status='failed', _regular_required=False)
    call['transport'].update(finished_at=stamp(.45), elapsed_seconds=.45, complete=False,
                             spans=phases()[:1], query_count=0)
    call['transport'].pop('result_received_at')
    call['transport'].pop('applied_at')
    snapshot = dict(status='failed', calls=[], transport_calls=[call], _regular_recorded=False)
    value = recognition_timeline(snapshot)
    spans = current(value)['spans']
    assert {span['lane'] for span in spans} == {'danzhengtong_seal'}
    assert [span['kind'] for span in spans] == ['lifecycle', 'request']
    assert value['elapsed_seconds'] == .45
