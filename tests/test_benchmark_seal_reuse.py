"""Benchmark guardrails without loading models, contacting providers or writing app data."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request

from PIL import Image
import pytest
import requests

from tools.analysis.operations import benchmark_seal_reuse as benchmark


def test_normalization_keeps_ocr_confidence_candidates_links_and_nested_timing(tmp_path):
    left_root, right_root = tmp_path / 'a', tmp_path / 'b'
    left = {'processing_seconds': 9, 'processing_timings': {'calls': 10},
            'seal_check': {'confidence': .90001, 'selected_id': 'candidate-A'},
            'artifacts': [{'id': 'candidate-A', 'path': str(left_root / 'artifacts/seal.png'),
                           'image_url': '/files/artifacts/benchmark-1/seal.png', 'processing_seconds': 3}],
            'temporary': '/tmp/receipt-four-way-first/coarse-90.png'}
    right = deepcopy(left)
    right.update(processing_seconds=2, processing_timings={'calls': 2},
                 temporary='/tmp/receipt-four-way-second/coarse-90.png')
    right['artifacts'][0]['path'] = str(right_root / 'artifacts/seal.png')
    first = benchmark.normalize_result(left, left_root)
    second = benchmark.normalize_result(right, right_root)
    assert first == second
    for mutate in (
        lambda value: value['seal_check'].update(confidence=.90002),
        lambda value: value['seal_check'].update(selected_id='candidate-B'),
        lambda value: value['artifacts'][0].update(image_url='/files/artifacts/benchmark-1/other.png'),
        lambda value: value['artifacts'][0].update(processing_seconds=4),
    ):
        changed = deepcopy(second); mutate(changed)
        assert benchmark.structure_differences(first, changed)
    assert benchmark.structure_differences({'a': True}, {'a': 1})
    assert benchmark.structure_differences([1, 2], [2, 1])
    assert benchmark.structure_differences({'a': 1}, {'a': 1, 'error': 'failed'})


def test_image_comparison_detects_single_pixels_missing_files_and_palette_changes(tmp_path):
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir(); second.mkdir()
    for root in (first, second):
        Image.new('RGB', (3, 3), 'white').save(root / 'same.png')
    assert not benchmark.compare_images(first, second)['differences']
    changed = Image.new('RGB', (3, 3), 'white'); changed.putpixel((2, 2), (254, 255, 255))
    changed.save(second / 'same.png')
    assert benchmark.compare_images(first, second)['differences'][0]['kind'] == 'pixels'
    (second / 'same.png').unlink()
    assert benchmark.compare_images(first, second)['differences'][0]['kind'] == 'missing_image'
    for root, color in ((first, [0, 0, 0]), (second, [255, 0, 0])):
        image = Image.new('P', (2, 2)); image.putpalette(color + [0] * 765); image.save(root / 'palette.png')
    assert any(row['path'] == 'palette.png' and row['kind'] == 'pixels'
               for row in benchmark.compare_images(first, second)['differences'])


@pytest.mark.parametrize('separator', ['/', '\\'])
def test_normalization_handles_both_path_spellings_without_changing_ocr(tmp_path, separator):
    root = tmp_path / 'output'
    path = str(root.resolve() / 'artifacts' / 'seal.png').replace('\\', '/').replace('/', separator)
    temporary = separator.join(['tmp', 'receipt-four-way-first', 'coarse-90.png'])
    value = {'path': path, 'ocr': r'公司\A/B', 'temporary': separator + temporary,
             'message': 'cannot read ' + path,
             'outside': str(root.resolve()) + '-other/seal.png'}
    actual = benchmark.normalize_result(value, root)
    assert actual['path'] == '<RUN>/artifacts/seal.png'
    assert actual['ocr'] == value['ocr'] and actual['outside'] == value['outside']
    assert actual['message'] == 'cannot read <RUN>/artifacts' + separator + 'seal.png'
    assert 'receipt-four-way-<RUN>' in actual['temporary']


def test_replay_returns_complete_independent_response_and_counts_calls(tmp_path):
    source = tmp_path / 'source.jpg'
    external = {'enabled': True, 'ok': True, 'response': {'data': [{'xyxy': [1, 2, 3, 4], 'text_formatted': '章'}]}}
    historical = {'seal_check': {'qingtong_channel': {'api': external}}}
    recorded = benchmark.recorded_response(historical)
    api = benchmark.RecordedSealApi(recorded, source)
    first = api.recognize(source); first['response']['data'][0]['xyxy'][0] = 99
    assert api.recognize(source) == external
    assert api.calls == 2 and recorded == external
    with pytest.raises(RuntimeError, match='No recorded'):
        api.recognize(tmp_path / 'other.jpg')


def test_readonly_connections_fail_writes_and_never_create_missing_databases(tmp_path):
    path = tmp_path / 'app.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE example(value)')
        connection.execute('INSERT INTO example VALUES(1)')
    before = hashlib.sha256(path.read_bytes()).digest()
    with benchmark.readonly_connection(path) as connection:
        assert connection.execute('SELECT value FROM example').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('DELETE FROM example')
    assert hashlib.sha256(path.read_bytes()).digest() == before
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError), benchmark.readonly_connection(missing):
        pass
    assert not missing.exists()


def test_loading_sample_and_standard_snapshots_does_not_write_or_load_the_web_app(tmp_path):
    import sys
    # The repository's shared conftest may already have imported the web app.
    web_before = sys.modules.get('receipt_ocr.web.application')
    source = tmp_path / 'data'; source.mkdir(); (source / 'one.jpg').write_bytes(b'original image')
    sample = tmp_path / 'sample.db'; production = tmp_path / 'production.db'
    config = {'fields': ['paddle_v6'], 'seal': ['paddle_v6', 'qingtong']}
    payload = {'recognition_config': config, 'ocr_backend': 'paddle_v6', 'seal_recognition_mode': 'qingtong',
               'seal_check': {'api': {'ok': True, 'response': {'data': [{'xyxy': [1, 2, 3, 4]}]}}}}
    with sqlite3.connect(sample) as connection:
        connection.execute('CREATE TABLE results(id INTEGER, filename TEXT, result_json TEXT)')
        connection.execute('INSERT INTO results VALUES(1,?,?)', ('one.jpg', json.dumps(payload)))
    with sqlite3.connect(production) as connection:
        connection.execute('CREATE TABLE seal_standards(requirement_key TEXT,image_name TEXT,shape TEXT)')
        connection.execute("INSERT INTO seal_standards VALUES('example','example.png','round')")
    before = [path.read_bytes() for path in (sample, production)]
    cases = benchmark.load_cases(sample, source, [1])
    standards = benchmark.load_standards(production)
    assert cases[0]['source'] == source / 'one.jpg'
    assert cases[0]['recognition_config'] == config
    assert standards['example']['image_url'] == '/files/seal-standards/example.png'
    assert [path.read_bytes() for path in (sample, production)] == before
    assert sys.modules.get('receipt_ocr.web.application') is web_before


def test_network_guard_blocks_http_tcp_and_urllib_but_allows_unix_ipc():
    original = socket.create_connection
    with patch.object(socket.socket, 'connect', lambda *args, **kwargs: 'unix-ok'):
        with benchmark.block_network() as attempts:
            with pytest.raises(RuntimeError, match='disabled'):
                requests.get('https://example.invalid/paid-api')
            with pytest.raises(RuntimeError, match='disabled'):
                urllib.request.urlopen('https://example.invalid')
            with pytest.raises(RuntimeError, match='disabled'):
                socket.create_connection(('127.0.0.1', 1))
            with socket.socket(socket.AF_INET) as connection:
                with pytest.raises(RuntimeError, match='disabled'):
                    connection.connect(('127.0.0.1', 1))
            if hasattr(socket, 'AF_UNIX'):
                assert socket.socket.connect(SimpleNamespace(family=socket.AF_UNIX), 'unused') == 'unix-ok'
            assert len(attempts) == 4
    assert socket.create_connection is original


def test_network_guard_still_blocks_ip_when_unix_sockets_are_unavailable(monkeypatch):
    monkeypatch.delattr(socket, 'AF_UNIX', raising=False)
    with benchmark.block_network() as attempts:
        with pytest.raises(RuntimeError, match='disabled'):
            socket.create_connection(('127.0.0.1', 1))
        with socket.socket(socket.AF_INET6) as connection:
            with pytest.raises(RuntimeError, match='disabled'):
                connection.connect(('::1', 1))
    assert len(attempts) == 2


def test_cache_bypass_accepts_new_kwargs_and_restores_original_after_failure():
    original = lambda *args, **kwargs: 'cached'
    runtime = SimpleNamespace(reuse_text_boxes=original)
    with pytest.raises(ValueError), benchmark.reuse_mode(runtime, False):
        assert runtime.reuse_text_boxes('source', ('engine', 'v6'), lambda: ('raw', (10, 20)), cacheable=lambda value: True) == ('raw', (10, 20))
        raise ValueError('test')
    assert runtime.reuse_text_boxes is original


def test_validation_rejects_equal_provider_failures_missing_inference_and_wrong_replay_counts():
    result = {'processing_timings': {'steps': {'paddle_text_inference': {'calls': 1, 'seconds': 1}}},
              'recognition_variants': {'seal': [{'method': 'paddle_v6', 'details': {}}]}}
    assert benchmark.validate_run(result, 1, 1) == (1, [])
    invalid = deepcopy(result); invalid['recognition_variants']['seal'][0]['error'] = 'missing model'
    assert benchmark.validate_run(invalid, 1, 1)[1]
    assert benchmark.validate_run(result, 2, 1)[1]
    assert benchmark.validate_run({}, 0, 0)[1]
    invalid = deepcopy(result); invalid['review_issues'] = [{'code': 'provider_failure', 'message': 'blocked network'}]
    assert benchmark.validate_run(invalid, 1, 1)[1]


def test_benchmark_alternates_orders_compares_warmup_and_never_hides_semantic_differences(tmp_path, monkeypatch):
    order = []

    def fake_run(case, directory, mode, standards, progress, label):
        directory.mkdir(parents=True)
        Image.new('RGB', (2, 2), 'white').save(directory / 'preview.png')
        order.append((case['result_id'], mode, label))
        return {'seal_check': {'selected_id': 'same', 'confidence': .9}}, {
            'mode': mode, 'wall_seconds': 1, 'result_id': case['result_id']}

    monkeypatch.setattr(benchmark, 'run_one', fake_run)
    cases = [{'result_id': 1}, {'result_id': 2}]
    report = benchmark.benchmark(cases, {}, tmp_path / 'good', repeats=1)
    assert report['status'] == 'passed' and report['network_attempts'] == 0
    assert [(row[0], row[1]) for row in order] == [(1, 'baseline'), (1, 'baseline'), (1, 'reuse'), (2, 'reuse'), (2, 'baseline')]
    assert report['comparisons'][0]['baseline_stability']['structure_differences'] == []
    assert (tmp_path / 'good/progress.jsonl').read_text().endswith('\n')

    def different(*args, **kwargs):
        result, metrics = fake_run(*args, **kwargs)
        if args[2] == 'reuse':
            result['seal_check']['selected_id'] = 'other-stamp'
        return result, metrics

    monkeypatch.setattr(benchmark, 'run_one', different)
    report = benchmark.benchmark(cases[:1], {}, tmp_path / 'bad', repeats=1)
    assert report['status'] == 'failed'
    assert report['comparisons'][0]['structure_differences'][0]['path'] == '$/seal_check/selected_id'
