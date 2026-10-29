"""Image timing accounts for real work without changing OCR invocation counts."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import local

import pytest

from receipt_ocr.runtime import calls, image_processing
from receipt_ocr.runtime.calls import capture_recognition_calls, recognition_call, record_model_initialization
from receipt_ocr.runtime.image_processing import image_operation, timed_image
from receipt_ocr.runtime.progress import model_stage


@pytest.fixture
def clock(monkeypatch):
    value = [0.0]
    monkeypatch.setattr(calls, 'perf_counter', lambda: value[0])
    monkeypatch.setattr(image_processing, 'perf_counter', lambda: value[0])
    return value


def by_operation(snapshot):
    return {step['operation']: step for step in snapshot['image_processing']['steps']}


def test_nested_image_steps_keep_exclusive_times_and_ocr_count(clock):
    with capture_recognition_calls() as captured, model_stage('seal', 'paddle_v6'):
        with image_operation('unwrap', '环形文字展开'):
            clock[0] += 1
            with image_operation('rotation', '旋转校正'):
                clock[0] += 2
            clock[0] += 3
        with image_operation('rotation', '旋转校正'):
            clock[0] += 1
    snapshot = captured.snapshot()
    steps = by_operation(snapshot)
    assert snapshot['image_processing']['seconds'] == 7
    assert steps['unwrap']['seconds'] == 4 and steps['unwrap']['calls'] == 1
    assert steps['rotation']['seconds'] == 3 and steps['rotation']['calls'] == 2
    assert steps['rotation']['stage'] == 'seal' and steps['rotation']['stage_label'] == '客户印章'
    assert snapshot['summary'] == dict(total=0, succeeded=0, failed=0, cache_hits=0)
    assert snapshot['processing_seconds'] == 7


def test_recognition_and_model_initialization_are_excluded_from_image_work(clock):
    with capture_recognition_calls() as captured:
        with image_operation('crop', '裁剪图片'):
            clock[0] += 1
            setup_start = clock[0]
            clock[0] += 20
            record_model_initialization(setup_start, clock[0])
            with image_operation('rotation', '旋转校正'):
                clock[0] += 2
                with recognition_call('paddle_v6', '识别文字'):
                    clock[0] += 8
            clock[0] += 3
    snapshot = captured.snapshot()
    assert snapshot['processing_seconds'] == 34
    assert snapshot['model_initialization_seconds'] == 20
    assert snapshot['image_processing']['seconds'] == 6
    assert by_operation(snapshot)['crop']['seconds'] == 4
    assert by_operation(snapshot)['rotation']['seconds'] == 2
    assert snapshot['summary']['total'] == 1 and snapshot['calls'][0]['seconds'] == 8


def test_overlapping_exclusions_are_subtracted_once(clock):
    with capture_recognition_calls() as captured:
        with image_operation('normalize', '形状校正'):
            clock[0] += 1
            with recognition_call('paddle_v6', '识别文字'):
                clock[0] += 2
                record_model_initialization(1, 3)
                clock[0] += 3
            clock[0] += 1
    assert captured.snapshot()['image_processing']['seconds'] == 2


def test_exception_keeps_completed_and_partial_image_work_without_error_text(clock):
    with pytest.raises(RuntimeError) as error:
        with capture_recognition_calls():
            with image_operation('crop', '裁剪图片'):
                clock[0] += 1
            with image_operation('denoise', '去噪处理'):
                clock[0] += 2
                raise RuntimeError('private path and secret')
    trace = error.value.recognition_calls
    assert trace['status'] == 'failed'
    assert trace['image_processing']['seconds'] == 3
    assert by_operation(trace)['crop']['failed'] == 0
    assert by_operation(trace)['denoise']['failed'] == 1
    assert 'secret' not in str(trace)


def test_nested_capture_reuses_timings_and_next_run_has_no_history(clock):
    @timed_image('rotation', '旋转校正')
    def rotate(value):
        clock[0] += 2
        return value + 1
    with capture_recognition_calls() as first:
        with capture_recognition_calls() as nested:
            assert nested is first
            assert rotate(4) == 5
    snapshot = first.snapshot()
    snapshot['image_processing']['steps'].clear()
    assert first.snapshot()['image_processing']['seconds'] == 2
    assert len(first.snapshot()['image_processing']['steps']) == 1
    with capture_recognition_calls() as second:
        pass
    assert second.snapshot()['image_processing'] == {'seconds': 0, 'steps': []}
    assert rotate(9) == 10
    assert first.snapshot()['image_processing']['seconds'] == 2


def test_parallel_contexts_aggregate_separately_by_stage_without_lost_updates(monkeypatch):
    clocks = local()
    def now():
        return getattr(clocks, 'value', 0.0)
    monkeypatch.setattr(calls, 'perf_counter', now)
    monkeypatch.setattr(image_processing, 'perf_counter', now)
    def perform(index):
        clocks.value = 0.0
        with model_stage('date' if index % 2 else 'seal', 'paddle_v6'):
            with image_operation('rotation', '旋转校正'):
                clocks.value += 1
                with image_operation('crop', '裁剪图片'):
                    clocks.value += 2
                clocks.value += 1
    with capture_recognition_calls() as captured:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(copy_context().run, perform, index) for index in range(40)]
            for future in futures:
                future.result()
    trace = captured.snapshot()
    rows = trace['image_processing']['steps']
    assert trace['image_processing']['seconds'] == 160
    assert len(rows) == 4
    assert all(row['calls'] == 20 and row['seconds'] == 40 for row in rows)
    assert trace['summary']['total'] == 0


def test_image_operations_outside_capture_do_not_allocate_a_trace(clock):
    with image_operation('rotation', '旋转校正'):
        clock[0] += 1
    with capture_recognition_calls() as captured:
        pass
    assert captured.snapshot()['image_processing'] == {'seconds': 0, 'steps': []}
