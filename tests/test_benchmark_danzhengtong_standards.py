"""Batch probing is bounded, resumable and never uses annotated metadata as input."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from receipt_ocr.danzhengtong import Settings
from tools.analysis.operations.benchmark_danzhengtong_standards import load_settings, run_benchmark


class Clock:
    seconds = 0.0

    def time(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds

    def now(self):
        return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)).isoformat()


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class Transport:
    def __init__(self, clock):
        self.clock = clock
        self.uploads, self.submits, self.queries, self.events = [], [], [], []
        self.active, self.max_active = set(), 0
        self.submit_failures, self.query_failures = {}, {}
        self.pending = 0
        self.output = None

    def request(self, method, url, **kwargs):
        self.clock.sleep(1)
        if 'files' in kwargs:
            filename, stream, _ = kwargs['files']['file']
            self.uploads.append((filename, stream.read()))
            self.events.append(('upload', filename))
            return Response({'status': True, 'fileId': 'file-' + filename,
                             'filePath': 'https://files.example/image?signature=upload-secret'})
        if method == 'POST':
            payload = kwargs['json']
            filename = payload['files'][0]['fileName']
            assert payload['modelId'] == 'configured-standard-seal'
            assert set(payload) == {'files', 'docType', 'appId', 'appKey', 'appSecret', 'callBackUrl', 'modelId', 'sysCode', 'orgId'}
            self.submits.append(filename)
            self.events.append(('submit', filename))
            failures = self.submit_failures.get(filename, [])
            if failures:
                failure = failures.pop(0)
                if isinstance(failure, Exception):
                    raise failure
                return Response(failure)
            uuid = 'uuid-' + filename
            self.active.add(uuid)
            self.max_active = max(self.max_active, len(self.active))
            return Response({'code': 200, 'status': True, 'data': {'reqUuid': uuid}})
        uuid = kwargs['params']['reqUuid']
        # The first GET is allowed only after its accepted ID is durable.
        if self.output:
            assert any(json.loads(path.read_text(encoding='utf-8')).get('req_uuid') == uuid
                       for path in self.output.glob('standard-*.json'))
        self.queries.append(uuid)
        self.events.append(('query', uuid))
        failures = self.query_failures.get(uuid, [])
        if failures:
            raise failures.pop(0)
        if self.pending:
            self.pending -= 1
            return Response({'code': 200, 'status': True, 'data': {'commitResult': None}})
        self.active.discard(uuid)
        return Response({'code': 200, 'status': True, 'data': {
            'commitResult': {'收货客户印章': {'value': '原始 返回文字', 'ratio': 0},
                             '印章类型': {'value': '椭圆章'}, '印章颜色': {'value': '蓝色'}},
            'imgUrls': ['http://10.0.0.1/result.png?signature=image-secret']}})


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    transport = Transport(clock)
    settings = Settings(app_id='app-unique-long', app_key='key-unique-long', app_secret='secret-unique-long',
                        key_id='gateway-unique-long', callback_url='https://example.com/callback',
                        seal_model_id='configured-standard-seal')
    manifest = tmp_path / 'manifest.json'
    records = []
    for index in range(1, 8):
        source = tmp_path / f'standard-{index:04d}.png'
        source.write_bytes(b'unchanged-original-image-' + str(index).encode())
        records.append({'standard_id': index, 'image_path': str(source), 'expected': 'DO-NOT-SEND-EXPECTED-ANSWER',
                        'image_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    manifest.write_text(json.dumps({'records': records}), encoding='utf-8')
    output = tmp_path / 'responses'
    transport.output = output
    arguments = dict(settings=settings, transport=transport, clock=clock.time, sleep=clock.sleep, now=clock.now,
                     wait_timeout_seconds=100, poll_interval_seconds=2)
    return manifest, output, transport, clock, settings, arguments


def test_limit_then_resume_full_batch_keeps_raw_fields_max_four_and_no_duplicate_posts(setup):
    manifest, output, transport, clock, settings, args = setup
    first = run_benchmark(manifest, output, limit=2, **args)
    assert first['counts']['completed'] == 2
    full = run_benchmark(manifest, output, **args)
    assert full['counts'] == dict(completed=7, waiting=0, failed=0, unknown=0, queued=0, total=7)
    assert len(transport.uploads) == len(transport.submits) == 7
    assert transport.max_active == 4
    report = json.loads((output / 'standard-0001.json').read_text(encoding='utf-8'))
    assert report['commitResult']['收货客户印章'] == {'value': '原始 返回文字', 'ratio': 0}
    assert report['response']['commitResult'] == report['commitResult']
    assert report['timings']['ocr_calls'] == 1 and report['timings']['query_count'] == 1
    assert report['timings']['submission_attempts'] == 1
    assert [event['phase'] for event in report['requests']] == ['upload', 'submit', 'query']
    assert report['timings']['request_seconds'] == 3
    assert report['timings']['elapsed_seconds'] >= 3
    serialized = ''.join(path.read_text(encoding='utf-8') for path in output.glob('*.json'))
    for secret in (settings.app_id, settings.app_key, settings.app_secret, settings.key_id, 'upload-secret', 'image-secret'):
        assert secret not in serialized


def test_code_9007_reuses_file_id_and_preserves_all_rejections_with_only_two_retries(setup):
    manifest, output, transport, clock, settings, args = setup
    rejected = {'code': 9007, 'status': False, 'message': '文件转储失败'}
    transport.submit_failures['standard-0001.png'] = [rejected, rejected]
    result = run_benchmark(manifest, output, limit=1, **args)
    assert result['counts']['completed'] == 1
    assert len(transport.uploads) == 1 and len(transport.submits) == 3
    report = json.loads((output / 'standard-0001.json').read_text(encoding='utf-8'))
    attempts = [event for event in report['requests'] if event['phase'] == 'submit']
    assert [event['response']['body']['code'] for event in attempts] == [9007, 9007, 200]
    assert report['retry_9007_count'] == 2
    assert report['timings']['ocr_calls'] == 1 and report['timings']['submission_attempts'] == 3
    assert clock.seconds >= 6 + len(report['requests'])


def test_exhausted_transfer_rejection_fails_that_record_and_continues_batch(setup):
    manifest, output, transport, clock, settings, args = setup
    transport.submit_failures['standard-0001.png'] = [{'code': 9007, 'status': False, 'message': '文件转储失败'}] * 3
    result = run_benchmark(manifest, output, limit=2, **args)
    assert result['counts']['failed'] == result['counts']['completed'] == 1
    assert transport.submits.count('standard-0001.png') == 3
    assert len(transport.uploads) == 2


def test_unknown_post_never_repeats_and_get_network_failure_retries_same_uuid(setup):
    manifest, output, transport, clock, settings, args = setup
    transport.submit_failures['standard-0001.png'] = [TimeoutError('lost POST response')]
    transport.query_failures['uuid-standard-0002.png'] = [TimeoutError('temporary GET timeout')]
    result = run_benchmark(manifest, output, limit=2, **args)
    assert result['counts']['unknown'] == result['counts']['completed'] == 1
    run_benchmark(manifest, output, limit=2, **args)
    assert len(transport.uploads) == len(transport.submits) == 2
    assert transport.queries == ['uuid-standard-0002.png'] * 2
    report = json.loads((output / 'standard-0002.json').read_text(encoding='utf-8'))
    assert report['timings']['query_count'] == 2 and report['timings']['ocr_calls'] == 1
    assert report['requests'][-2]['status'] == 'failed'


def test_wait_timeout_remains_waiting_and_resume_only_queries_accepted_uuid(setup):
    manifest, output, transport, clock, settings, args = setup
    transport.pending = 100
    args['wait_timeout_seconds'] = 6
    waiting = run_benchmark(manifest, output, limit=1, **args)
    assert waiting['counts']['waiting'] == 1 and waiting['counts']['failed'] == 0
    report = json.loads((output / 'standard-0001.json').read_text(encoding='utf-8'))
    assert report['wait_timeout'] is True
    transport.pending = 0
    resumed = run_benchmark(manifest, output, limit=1, **args)
    assert resumed['counts']['completed'] == 1
    assert len(transport.uploads) == len(transport.submits) == 1


def test_four_expired_waits_do_not_prevent_later_manifest_records_from_being_tested(setup):
    manifest, output, transport, clock, settings, args = setup
    transport.pending = 1000
    args['wait_timeout_seconds'] = 4
    result = run_benchmark(manifest, output, **args)
    assert result['counts']['waiting'] == 7 and result['counts']['queued'] == 0
    assert len(transport.uploads) == len(transport.submits) == 7
    assert result['counts']['failed'] == result['counts']['unknown'] == 0
    for path in output.glob('standard-*.json'):
        report = json.loads(path.read_text(encoding='utf-8'))
        assert report['wait_timeout'] and report['req_uuid']
        assert report['runs'][-1]['elapsed_seconds'] <= result['elapsed_seconds']


def test_per_record_elapsed_ends_at_its_last_request_not_at_batch_completion(setup):
    manifest, output, transport, clock, settings, args = setup
    result = run_benchmark(manifest, output, **args)
    first = json.loads((output / 'standard-0001.json').read_text(encoding='utf-8'))
    run = first['runs'][-1]
    assert run['request_count'] == 3
    assert run['finished_at'] == first['requests'][-1]['finished_at']
    assert run['elapsed_seconds'] < result['elapsed_seconds']


def test_crashed_submit_marker_is_unknown_and_input_mutation_refuses_resume(setup):
    manifest, output, transport, clock, settings, args = setup
    run_benchmark(manifest, output, limit=1, **args)
    path = output / 'standard-0001.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    report.update(status='submitting', req_uuid='')
    path.write_text(json.dumps(report), encoding='utf-8')
    result = run_benchmark(manifest, output, limit=1, **args)
    assert result['counts']['unknown'] == 1 and len(transport.submits) == 1
    Path(report['input_path']).write_bytes(b'mutated')
    with pytest.raises(ValueError, match='已有报告不一致'):
        run_benchmark(manifest, output, limit=1, **args)
    assert len(transport.submits) == 1


def test_software_credentials_are_read_only_and_configured_seal_scene_wins(tmp_path):
    path = tmp_path / 'settings.db'
    settings = Settings(app_id='software-app', seal_model_id='software-seal', model_id='page-scene')
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT)')
        connection.execute('INSERT INTO app_settings VALUES(?,?)', ('danzhengtong_credentials', json.dumps(asdict(settings))))
    before = path.read_bytes()
    loaded, source = load_settings(path)
    assert source == 'software_database' and loaded.model_id == loaded.seal_model_id == 'software-seal'
    assert path.read_bytes() == before
