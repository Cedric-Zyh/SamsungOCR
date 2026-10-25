"""Offline, read-only A/B validation of request-scoped Paddle reuse.

Replays recorded QingTong responses, runs real local OCR, and compares the full
result plus every generated image. No Flask application or job worker is loaded.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
from contextlib import contextmanager, ExitStack, nullcontext, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import re
import socket
import sqlite3
import statistics
import threading
import time
from unittest.mock import patch
import urllib.request


DEFAULT_RESULT_IDS = (1858, 1857, 1846, 1842, 1843, 1875, 1814)
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
TEMP_DIRECTORY = re.compile(r'(?<=/)(receipt-four-way-|seal-horizontal-|receipt-printed-page-|receipt-grade-)[A-Za-z0-9_-]+(?=/|$)')


@contextmanager
def readonly_connection(path):
    """Never create, migrate, recover or write a business database."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    try:
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.close()


@contextmanager
def block_network():
    """Fail even if a future provider accidentally bypasses RecordedSealApi."""
    attempts = []
    lock = threading.Lock()
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def denied(*args, **kwargs):
        with lock:
            attempts.append('network request blocked')
        raise RuntimeError('Network is disabled for the offline OCR benchmark')

    def ip_guard(original):
        def connect(connection, *args, **kwargs):
            if connection.family in {socket.AF_INET, socket.AF_INET6}:
                return denied()
            return original(connection, *args, **kwargs)
        return connect

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, 'connect', ip_guard(original_connect)))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', ip_guard(original_connect_ex)))
        stack.enter_context(patch.object(socket, 'create_connection', denied))
        stack.enter_context(patch.object(urllib.request, 'urlopen', denied))
        import requests.sessions
        stack.enter_context(patch.object(requests.sessions.Session, 'request', denied))
        yield attempts


def recorded_response(result):
    check = result.get('seal_check') or {}
    candidates = [(check.get('qingtong_channel') or {}).get('api'), check.get('api')]
    candidates.extend(((variant.get('details') or {}).get('seal_check') or {}).get('api')
                      for variant in (result.get('recognition_variants') or {}).get('seal', []))
    return next((deepcopy(value) for value in candidates if isinstance(value, dict)
                 and isinstance(value.get('response'), (dict, list))), None)


class RecordedSealApi:
    enabled = True

    def __init__(self, payload, source):
        self.payload, self.source, self.calls = deepcopy(payload), Path(source).resolve(), 0

    def recognize(self, source):
        if Path(source).resolve() != self.source or self.payload is None:
            raise RuntimeError('No recorded QingTong response for this source')
        self.calls += 1
        return deepcopy(self.payload)

    @staticmethod
    def skipped():
        return {'enabled': False, 'requested': False, 'message': '本次选择仅使用本地印章识别'}


def _has_table(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load_cases(database, data_dir, result_ids):
    cases = []
    with readonly_connection(database) as connection:
        for result_id in result_ids:
            row = connection.execute('SELECT filename,result_json FROM results WHERE id=?', (result_id,)).fetchone()
            if row is None:
                raise ValueError(f'Sample result {result_id} does not exist')
            filename, historical = row['filename'], json.loads(row['result_json'])
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError(f'Unsafe sample filename at result {result_id}')
            library = (connection.execute('SELECT storage_name,deleted_at FROM sample_library WHERE filename=?', (filename,)).fetchone()
                       if _has_table(connection, 'sample_library') else None)
            if library and library['deleted_at']:
                raise ValueError(f'Sample {filename} has been deleted')
            name = library['storage_name'] if library and library['storage_name'] else filename
            root = Path(database).resolve().parent / 'originals' if library and library['storage_name'] else Path(data_dir).resolve()
            source = (root / name).resolve()
            if source.parent != root.resolve() or not source.is_file():
                raise ValueError(f'Sample source is missing or outside its directory: {filename}')
            job = (connection.execute('SELECT payload_json FROM recognition_jobs WHERE result_id=? ORDER BY rowid DESC LIMIT 1', (result_id,)).fetchone()
                   if _has_table(connection, 'recognition_jobs') else None)
            options = json.loads(job['payload_json']) if job else {}
            config = historical.get('recognition_config') or options.get('recognition_config')
            if not isinstance(config, dict):
                raise ValueError(f'Result {result_id} has no saved recognition plan')
            if any('danzhengtong' in value for value in config.values() if isinstance(value, list)):
                raise ValueError(f'Result {result_id} requires an unrecorded Danzhengtong provider')
            external = recorded_response(historical)
            if 'qingtong' in config.get('seal', []) and external is None:
                raise ValueError(f'Result {result_id} has no complete recorded QingTong response')
            cases.append({'result_id': result_id, 'filename': filename, 'source': source,
                          'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                          'recognition_config': config, 'external': external,
                          'previous_fields': options.get('previous_fields'),
                          'ocr_backend': options.get('ocr_backend') or historical.get('ocr_backend'),
                          'seal_recognition_mode': options.get('seal_recognition_mode') or historical.get('seal_recognition_mode')})
    return cases


def load_standards(database):
    from receipt_ocr.persistence.seal_standards import standard_record
    with readonly_connection(database) as connection:
        return {row['requirement_key']: standard_record(row) for row in connection.execute('SELECT * FROM seal_standards')}


def normalize_result(result, artifact_root):
    """Drop only measured runtime fields; retain OCR, IDs, links and evidence."""
    root = str(Path(artifact_root).resolve())

    def normalize(value, top=False):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()
                    if not (top and key in {'processing_seconds', 'processing_timings'})}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            value = value.replace(root + '/', '<RUN>/')
            if value == root:
                value = '<RUN>'
            return TEMP_DIRECTORY.sub(lambda match: match.group(1) + '<RUN>', value)
        return value

    return normalize(result, top=True)


def structure_differences(left, right, path='$'):
    """Exact recursive comparison, including types, list order and confidence."""
    if type(left) is not type(right):
        return [{'path': path, 'kind': 'type', 'baseline': left, 'reuse': right}]
    if isinstance(left, dict):
        differences = []
        for key in sorted(left.keys() | right.keys()):
            location = f'{path}/{key}'
            if key not in left or key not in right:
                differences.append({'path': location, 'kind': 'missing_key', 'baseline_present': key in left, 'reuse_present': key in right})
            else:
                differences.extend(structure_differences(left[key], right[key], location))
        return differences
    if isinstance(left, list):
        differences = [] if len(left) == len(right) else [{'path': path, 'kind': 'length', 'baseline': len(left), 'reuse': len(right)}]
        for index, (first, second) in enumerate(zip(left, right)):
            differences.extend(structure_differences(first, second, f'{path}/{index}'))
        return differences
    return [] if left == right else [{'path': path, 'kind': 'value', 'baseline': left, 'reuse': right}]


def compare_images(baseline_root, reuse_root):
    from PIL import Image
    roots = (Path(baseline_root), Path(reuse_root))
    files = [{path.relative_to(root).as_posix(): path for path in root.rglob('*')
              if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES} for root in roots]
    differences = []
    for name in sorted(files[0].keys() | files[1].keys()):
        if name not in files[0] or name not in files[1]:
            differences.append({'path': name, 'kind': 'missing_image', 'baseline_present': name in files[0], 'reuse_present': name in files[1]})
            continue
        with Image.open(files[0][name]) as first, Image.open(files[1][name]) as second:
            first.load(); second.load()
            if first.mode != second.mode or first.size != second.size:
                differences.append({'path': name, 'kind': 'image_shape', 'baseline': [first.mode, first.size], 'reuse': [second.mode, second.size]})
            else:
                first_pixels = first.convert('RGBA').tobytes() if first.mode in {'P', 'PA'} else first.tobytes()
                second_pixels = second.convert('RGBA').tobytes() if second.mode in {'P', 'PA'} else second.tobytes()
                if first_pixels != second_pixels:
                    differences.append({'path': name, 'kind': 'pixels',
                                        'baseline_sha256': hashlib.sha256(first_pixels).hexdigest(),
                                        'reuse_sha256': hashlib.sha256(second_pixels).hexdigest()})
    return {'baseline_images': len(files[0]), 'reuse_images': len(files[1]), 'differences': differences}


@contextmanager
def reuse_mode(runtime, enabled):
    if not hasattr(runtime, 'reuse_text_boxes'):
        raise RuntimeError('This benchmark requires paddle_runtime.reuse_text_boxes')
    bypass = lambda source, model_signature, recognize, **kwargs: recognize()
    with (nullcontext() if enabled else patch.object(runtime, 'reuse_text_boxes', bypass)):
        yield


class ProgressLog:
    def __init__(self, path):
        self.file = Path(path).open('w', encoding='utf-8')
        self.lock = threading.Lock()

    def emit(self, event, **details):
        row = {'event': event, 'time_utc': datetime.now(timezone.utc).isoformat(), **details}
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self.lock:
            self.file.write(line + '\n'); self.file.flush()
            print(line, flush=True)

    def close(self):
        self.file.close()


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


@contextmanager
def capture_stderr(path):
    """Capture Python warnings and native model stderr in this isolated process."""
    with Path(path).open('w', encoding='utf-8') as stream:
        saved = os.dup(2)
        try:
            os.dup2(stream.fileno(), 2)
            with redirect_stderr(stream):
                yield
        finally:
            stream.flush()
            os.dup2(saved, 2)
            os.close(saved)


def validate_run(result, replay_calls, expected_replays):
    errors = []
    calls = sum(value.get('calls', 0) for name, value in
                (result.get('processing_timings') or {}).get('steps', {}).items()
                if name.startswith('paddle_') and name.endswith('_inference'))
    if calls <= 0:
        errors.append('No actual local Paddle inference was measured')
    if replay_calls != expected_replays:
        errors.append(f'Recorded API calls: expected {expected_replays}, got {replay_calls}')
    if result.get('overall') == '识别失败':
        errors.append('Recognition failed')
    for stage, variants in (result.get('recognition_variants') or {}).items():
        for variant in variants if isinstance(variants, list) else []:
            if variant.get('error'):
                errors.append(f"{stage}/{variant.get('method')}: {variant['error']}")
    for name in ('review_issues', 'stage_review_issues'):
        for issue in result.get(name) or []:
            if isinstance(issue, dict) and issue.get('code') == 'provider_failure':
                errors.append(f"Provider failure: {issue.get('message', issue)}")
    for stage, status in (result.get('recognition_status') or {}).items():
        if status == '识别失败':
            errors.append(f'{stage}: recognition failed')
    return calls, list(dict.fromkeys(errors))


def run_one(case, directory, mode, standards, progress, label):
    from receipt_ocr.application.stage_coordinator import ReceiptAnalyzer
    from receipt_ocr.domain.seal_standards import apply_standard_check, requirement_key
    from receipt_ocr.providers import paddle_runtime
    from receipt_ocr.runtime.progress import progress_reporting

    directory.mkdir(parents=True, exist_ok=False)
    if hashlib.sha256(case['source'].read_bytes()).hexdigest() != case['source_sha256']:
        raise RuntimeError('Source pixels changed since the benchmark manifest was loaded')
    external = RecordedSealApi(case['external'], case['source'])
    analyzer = ReceiptAnalyzer(seal_api=external)
    progress.emit('run_started', label=label, result_id=case['result_id'], filename=case['filename'], mode=mode)

    def report(event):
        if event.get('phase') in {'started', 'completed', 'failed'}:
            progress.emit('stage', label=label, result_id=case['result_id'], mode=mode, **event)

    started = time.perf_counter()
    with capture_stderr(directory / 'stderr.log'), reuse_mode(paddle_runtime, mode == 'reuse'), progress_reporting(report):
        result = analyzer.analyze(
            case['source'], directory / 'preview.jpg', filename=case['filename'],
            recognition_config=deepcopy(case['recognition_config']),
            previous_fields=deepcopy(case['previous_fields']),
            ocr_backend=case['ocr_backend'], seal_recognition_mode=case['seal_recognition_mode'],
            artifact_dir=directory / 'artifacts',
            artifact_url_prefix=f"/files/artifacts/benchmark-{case['result_id']}",
            reference_matcher=None,
        )
        requirement = (result.get('fields') or {}).get('签章要求', '')
        result = apply_standard_check(result, deepcopy(standards.get(requirement_key(requirement))))
    elapsed = time.perf_counter() - started
    normalized = normalize_result(result, directory)
    inference_calls, errors = validate_run(result, external.calls,
                                          int('qingtong' in case['recognition_config'].get('seal', [])))
    _save(directory / 'result.json', result)
    _save(directory / 'normalized.json', normalized)
    metrics = {'result_id': case['result_id'], 'filename': case['filename'], 'mode': mode,
               'label': label, 'wall_seconds': round(elapsed, 6),
               'recorded_api_calls': external.calls, 'processing_timings': result.get('processing_timings', {}),
               'local_inference_calls': inference_calls, 'provider_errors': errors,
               'directory': str(directory)}
    _save(directory / 'metrics.json', metrics)
    progress.emit('run_completed', **metrics)
    if errors:
        _save(directory / 'provider_errors.json', errors)
        raise RuntimeError(f"Invalid run {label}: {'; '.join(errors)}")
    return normalized, metrics


def benchmark(cases, standards, output, repeats=2):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    progress = ProgressLog(output / 'progress.jsonl')
    report = {'status': 'running', 'scope': {
        'local_ocr': 'real, same image and saved recognition plan',
        'qingtong': 'complete historical response replay; no network requests',
        'standards': 'read-only production standard snapshot; apply_standard_check enabled',
        'visual_reference_matcher': 'not included; reference_matcher=None in both modes',
        'comparison': 'all result structure except root processing_seconds/processing_timings and temporary directory identities; exact decoded image pixels',
    }, 'runs': [], 'comparisons': [], 'errors': []}
    try:
        with block_network() as attempts:
            progress.emit('warmup_started', output=str(output), result_ids=[case['result_id'] for case in cases])
            warmup, _ = run_one(cases[0], output / 'warmup', 'baseline', standards, progress, 'warmup')
            if attempts:
                raise RuntimeError('A provider attempted network access during warmup')
            progress.emit('warmup_completed')
            anchors = {cases[0]['result_id']: (warmup, output / 'warmup')}
            for repeat in range(repeats):
                for index, case in enumerate(cases):
                    modes = ('baseline', 'reuse') if (repeat + index) % 2 == 0 else ('reuse', 'baseline')
                    pair = {}
                    for mode in modes:
                        label = f"result-{case['result_id']}-repeat-{repeat + 1}-{mode}"
                        directory = output / label
                        normalized, metrics = run_one(case, directory, mode, standards, progress, label)
                        if attempts:
                            raise RuntimeError('A provider attempted network access during recognition')
                        report['runs'].append(metrics)
                        pair[mode] = (normalized, directory)
                    first, second = pair['baseline'], pair['reuse']
                    comparison = {'result_id': case['result_id'], 'repeat': repeat + 1,
                                  'structure_differences': structure_differences(first[0], second[0]),
                                  'images': compare_images(first[1], second[1])}
                    if case['result_id'] in anchors:
                        anchor, anchor_dir = anchors[case['result_id']]
                        comparison['baseline_stability'] = {
                            'structure_differences': structure_differences(anchor, first[0]),
                            'images': compare_images(anchor_dir, first[1])}
                    else:
                        anchors[case['result_id']] = first
                    comparison['equal'] = not (comparison['structure_differences'] or comparison['images']['differences']
                        or comparison.get('baseline_stability', {}).get('structure_differences')
                        or comparison.get('baseline_stability', {}).get('images', {}).get('differences'))
                    report['comparisons'].append(comparison)
                    progress.emit('comparison', result_id=case['result_id'], repeat=repeat + 1,
                                  equal=comparison['equal'], structure_differences=len(comparison['structure_differences']),
                                  image_differences=len(comparison['images']['differences']))
                    report['network_attempts'] = len(attempts)
                    _save(output / 'report.json', report)
            report['network_attempts'] = len(attempts)
            report['status'] = 'passed' if not attempts and all(item['equal'] for item in report['comparisons']) else 'failed'
        for mode in ('baseline', 'reuse'):
            seconds = [run['wall_seconds'] for run in report['runs'] if run['mode'] == mode]
            report.setdefault('wall_seconds', {})[mode] = {'median': statistics.median(seconds), 'total': sum(seconds)}
    except Exception as exc:
        report['status'] = 'failed'
        report['network_attempts'] = len(attempts) if 'attempts' in locals() else 0
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        progress.emit('failed', error=report['errors'][-1])
        raise
    finally:
        _save(output / 'report.json', report)
        progress.emit('finished', status=report['status'], report=str(output / 'report.json'))
        progress.close()
    return report


def parse_result_ids(values):
    return list(dict.fromkeys(int(value) for item in values for value in item.split(',') if value.strip()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-ids', nargs='+', default=[','.join(map(str, DEFAULT_RESULT_IDS))])
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--sample-db', type=Path, default=PROJECT_ROOT / 'storage/sample-center/results.db')
    parser.add_argument('--production-db', type=Path, default=PROJECT_ROOT / 'storage/results.db')
    parser.add_argument('--data-dir', type=Path, default=PROJECT_ROOT / '数据')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'output/ocr-reuse-benchmark')
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    ids = parse_result_ids(args.result_ids)
    if not ids:
        parser.error('--result-ids must not be empty')
    cases = load_cases(args.sample_db, args.data_dir, ids)
    standards = load_standards(args.production_db)
    directory = args.output.resolve() / (datetime.now(timezone.utc).strftime('run-%Y%m%d-%H%M%S-') + str(time.time_ns())[-8:])
    directory.mkdir(parents=True)
    from receipt_ocr.providers import paddle_runtime
    versions = {}
    for name in ('paddleocr', 'paddlex', 'paddlepaddle', 'onnxruntime', 'opencv-contrib-python', 'Pillow'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    _save(directory / 'manifest.json', {'result_ids': ids, 'repeats': args.repeats, 'versions': versions,
        'paddle_engine': paddle_runtime.paddle_engine(), 'model_variants': paddle_runtime.MODEL_VARIANTS,
        'device': 'cpu',
        'sample_database': str(args.sample_db.resolve()), 'production_database': str(args.production_db.resolve()),
        'standard_snapshot_sha256': hashlib.sha256(json.dumps(standards, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'cases': [{key: str(value) if isinstance(value, Path) else value
                   for key, value in case.items() if key != 'external'} for case in cases]})
    report = benchmark(cases, standards, directory, args.repeats)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
