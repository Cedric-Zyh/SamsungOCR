"""Profile each OCR request on one saved sample without changing business data.

Uses the benchmark's read-only loaders and offline replay. Instrumentation is
installed only in this process, after an ordinary warmup run.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import inspect
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.analysis.operations import benchmark_seal_reuse as benchmark


class OcrTrace:
    def __init__(self, output):
        self.output = Path(output)
        self.calls = []
        self.images = {}
        self.overhead_seconds = 0.0
        self.started = time.perf_counter()
        self.active = ContextVar('profile_ocr_request', default=None)
        self.phase = ContextVar('profile_ocr_phase', default='')
        self.seal_index = ContextVar('profile_seal_index', default=None)

    def _context(self, function, phase=None, region=False):
        @wraps(function)
        def wrapped(*args, **kwargs):
            variable = self.seal_index if region else self.phase
            value = (kwargs.get('index', args[2] if len(args) > 2 else None) if region
                     else phase(*args, **kwargs) if callable(phase) else phase)
            token = variable.set(value)
            try:
                return function(*args, **kwargs)
            finally:
                variable.reset(token)
        return wrapped

    def _metadata(self, path, kind):
        from PIL import Image
        phase, angle, stack_names = self.phase.get(), None, []
        frame = inspect.currentframe().f_back
        try:
            while frame:
                name = frame.f_code.co_name
                stack_names.append(name)
                if angle is None and name == '_four_way_candidate':
                    angle = frame.f_locals.get('angle')
                elif angle is None and name == 'prepare_horizontal_consensus':
                    seed = frame.f_locals.get('seed') if kind == 'line' else None
                    angle = seed.get('coarse') if isinstance(seed, dict) else frame.f_locals.get('coarse')
                frame = frame.f_back
        finally:
            del frame
        if not phase:
            if 'date' in str(path).lower() or any('date' in name for name in stack_names):
                phase = '日期区域'
            elif 'page-color-clean' in path.name:
                phase = '整页文字'
            elif self.seal_index.get() is not None or 'seal' in str(path).lower():
                phase = '印章版式与文字'
            else:
                phase = '其他文字'
        with Image.open(path) as image:
            width, height = image.size
        if kind == 'line':
            scale = '裁剪文字行'
        elif path.stem.endswith('-detection'):
            scale = '缩略图'
        else:
            scale = '当前原尺寸'
        return dict(phase=phase, angle=angle, scale=scale, width=width, height=height,
                    seal_index=self.seal_index.get(), source_path=str(path),
                    source_name=path.name)

    def _request(self, function, kind):
        @wraps(function)
        def wrapped(source, *args, **kwargs):
            from receipt_ocr.runtime.execution import _current
            overhead = time.perf_counter()
            path = Path(source)
            row = dict(id=len(self.calls) + 1, kind=kind, native_seconds=0.0,
                       setup_seconds=0.0, queue_seconds=0.0, cache_hit=False,
                       measurements=[], texts=[], result_count=0)
            row.update(self._metadata(path, kind))
            self.calls.append(row)
            execution = _current.get()
            hits = execution.text_box_cache_hits if execution else 0
            token = self.active.set(row)
            self.overhead_seconds += time.perf_counter() - overhead
            started = time.perf_counter()
            row['start_seconds'] = started - self.started
            try:
                result = function(source, *args, **kwargs)
                row['elapsed_seconds'] = time.perf_counter() - started
            except Exception as exc:
                row['elapsed_seconds'] = time.perf_counter() - started
                row['error'] = f'{type(exc).__name__}: {exc}'
                raise
            else:
                overhead = time.perf_counter()
                row['result_count'] = len(result)
                row['texts'] = [str(item.get('text', '') if isinstance(item, dict)
                                    else getattr(item, 'text', '')) for item in result]
                row['cache_hit'] = bool(execution and execution.text_box_cache_hits > hits)
                self.overhead_seconds += time.perf_counter() - overhead
                return result
            finally:
                self.active.reset(token)
                overhead = time.perf_counter()
                # Retain bytes before temporary directories disappear. Disk
                # report writes happen after recognition; neither is timed as
                # model inference or included in this request's elapsed time.
                relative = f'inputs/{row["id"]:04d}{path.suffix.lower()}'
                self.images[relative] = path.read_bytes()
                row['input_image'] = relative
                self.overhead_seconds += time.perf_counter() - overhead
        return wrapped

    @contextmanager
    def installed(self):
        from receipt_ocr.providers import paddle_runtime as runtime
        from receipt_ocr.recognition.seal.preprocess import inputs
        original_measure = runtime.measure

        @contextmanager
        def measure(name):
            row = self.active.get()
            elapsed = None
            try:
                with original_measure(name):
                    started = time.perf_counter()
                    try:
                        yield
                    finally:
                        elapsed = time.perf_counter() - started
            finally:
                if row is not None and elapsed is not None:
                    row['measurements'].append(dict(name=name, seconds=elapsed))
                    if name.endswith('_inference'):
                        row['native_seconds'] += elapsed
                    elif name.endswith('_setup'):
                        row['setup_seconds'] += elapsed
                    elif name == 'paddle_queue_wait':
                        row['queue_seconds'] += elapsed

        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, 'measure', measure))
            for name, kind in [('detect_text_boxes', 'boxes'), ('recognize_text', 'text'),
                               ('recognize_line', 'line'), ('recognize_seal_text', 'seal')]:
                stack.enter_context(patch.object(runtime, name, self._request(getattr(runtime, name), kind)))
            stack.enter_context(patch.object(inputs, 'prepare_inputs',
                                            self._context(inputs.prepare_inputs, region=True)))
            # pipeline imports prepare_inputs by value; instrument that alias
            # as well so later OCR reads retain their own filename-based phase.
            from receipt_ocr.recognition.seal import pipeline
            stack.enter_context(patch.object(pipeline, 'prepare_inputs', inputs.prepare_inputs))
            stack.enter_context(patch.object(inputs, 'prepare_round_stamp_combined', self._context(
                inputs.prepare_round_stamp_combined,
                phase=lambda *a, **kw: '增强图方向重试' if kw.get('retry_large_angles') else '首次方向搜索')))
            stack.enter_context(patch.object(inputs, 'prepare_horizontal_consensus', self._context(
                inputs.prepare_horizontal_consensus, phase='横排交叉核验')))
            self.started = time.perf_counter()
            yield self

    def save_images(self):
        for name, content in self.images.items():
            target = self.output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def validate_trace(calls, timings):
    counts, seconds = Counter(), defaultdict(float)
    for call in calls:
        for item in call['measurements']:
            counts[item['name']] += 1
            seconds[item['name']] += item['seconds']
    expected = {name: values for name, values in timings['steps'].items()
                if name.startswith('paddle_') and name.endswith('_inference')}
    actual = {name: count for name, count in counts.items() if name.endswith('_inference')}
    return dict(
        native_calls_match=actual == {name: value['calls'] for name, value in expected.items()},
        native_seconds_match=all(abs(seconds[name] - value['seconds']) < .02
                                 for name, value in expected.items()),
        cache_hits_match=sum(call['cache_hit'] for call in calls)
                         == timings.get('text_box_ocr_cache_hits', 0),
        measured_native_calls=actual,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-id', type=int, default=1858)
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'output/ocr-call-profile')
    args = parser.parse_args(argv)
    output = args.output.resolve() / (datetime.now(timezone.utc).strftime('run-%Y%m%d-%H%M%S-')
                                      + str(time.time_ns())[-8:])
    output.mkdir(parents=True)
    case = benchmark.load_cases(PROJECT_ROOT / 'storage/sample-center/results.db',
                                PROJECT_ROOT / '数据', [args.result_id])[0]
    standards = benchmark.load_standards(PROJECT_ROOT / 'storage/results.db')
    benchmark._save(output / 'manifest.json', {key: str(value) if isinstance(value, Path) else value
                                             for key, value in case.items() if key != 'external'})
    progress = benchmark.ProgressLog(output / 'progress.jsonl')
    try:
        with benchmark.block_network() as attempts:
            warmup, warm_metrics = benchmark.run_one(case, output / 'warmup', 'reuse', standards,
                                                       progress, 'warmup')
            trace = OcrTrace(output)
            with trace.installed():
                measured, metrics = benchmark.run_one(case, output / 'measured', 'reuse', standards,
                                                       progress, 'measured')
            differences = benchmark.structure_differences(warmup, measured)
            images = benchmark.compare_images(output / 'warmup', output / 'measured')
        trace.save_images()
        result = json.loads((output / 'measured/result.json').read_text())
        timings = metrics['processing_timings']
        checks = validate_trace(trace.calls, timings)
        checks.update(structure_equal=not differences, images_equal=not images['differences'],
                      image_pairs=images['baseline_images'], no_network=not attempts,
                      no_request_errors=not any(call.get('error') for call in trace.calls))
        report = dict(filename=case['filename'], result_id=case['result_id'],
                      wall_seconds=metrics['wall_seconds'], recognition_seconds=result['processing_seconds'],
                      warmup_seconds=warm_metrics['wall_seconds'], trace_overhead_seconds=trace.overhead_seconds,
                      network_attempts=len(attempts), checks=checks, timings=timings, calls=trace.calls,
                      structure_differences=differences, image_differences=images['differences'],
                      output=str(output), source_sha256=case['source_sha256'],
                      recorded_api_calls=metrics['recorded_api_calls'])
        benchmark._save(output / 'profile.json', report)
        from tools.analysis.operations.ocr_profile_report import write_report
        html = write_report(report, output)
        progress.emit('profile_completed', report=str(output / 'profile.json'), html=str(html), checks=checks)
        required = ('structure_equal', 'images_equal', 'native_calls_match', 'native_seconds_match',
                    'cache_hits_match', 'no_network', 'no_request_errors')
        return 0 if all(checks[key] for key in required) else 1
    finally:
        progress.close()


if __name__ == '__main__':
    raise SystemExit(main())
