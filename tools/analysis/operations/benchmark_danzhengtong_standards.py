"""Benchmark original standard PNGs through the configured seal scene.

Every HTTP request is serial. Up to four accepted documents may wait at once;
accepted UUIDs and uncertain submissions are durable before further work.
No production settings/database writes or returned-image downloads are made.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from receipt_ocr.danzhengtong import Client, Settings
from receipt_ocr.danzhengtong_errors import safe_error_text
from receipt_ocr.danzhengtong_seal import seal_settings
from .probe_danzhengtong_seal import identity, sanitized, timestamp, update_totals, write_json

DATABASE = PROJECT_ROOT / 'storage' / 'results.db'
TERMINAL = {'completed', 'failed', 'unknown'}


def load_settings(database=DATABASE):
    path = Path(database).resolve()
    if path.is_file():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone()
            row = connection.execute("SELECT value FROM app_settings WHERE key='danzhengtong_credentials'").fetchone() if exists else None
        if row:
            saved = json.loads(row[0])
            if not isinstance(saved, dict):
                raise ValueError('软件保存的单证通配置格式错误')
            return seal_settings(Settings(**saved)), 'software_database'
    return seal_settings(Settings.load()), 'environment_or_local_file'


class RecordingTransport:
    """Keep the actual HTTP response even when Client raises on business codes."""
    def __init__(self, transport):
        self.transport = transport
        self.last_response = None

    def request(self, method, url, **kwargs):
        self.last_response = None
        response = self.transport.request(method, url, **kwargs)
        try:
            body = response.json()
        except Exception:
            body = {'non_json_response': str(getattr(response, 'text', ''))[:2000]}
        self.last_response = {'http_status': getattr(response, 'status_code', None), 'body': deepcopy(body)}
        return response


class Benchmark:
    def __init__(self, manifest, output, settings, settings_source, *, max_in_flight=4,
                 poll_interval_seconds=2, wait_timeout_seconds=600, limit=None,
                 transport=None, clock=None, sleep=None, now=None):
        self.manifest, self.output = Path(manifest).resolve(), Path(output).resolve()
        self.settings, self.settings_source = seal_settings(settings), settings_source
        self.max_in_flight = int(max_in_flight)
        if not 1 <= self.max_in_flight <= 4:
            raise ValueError('同时等待数量应为 1 至 4')
        self.poll_interval, self.wait_timeout = float(poll_interval_seconds), float(wait_timeout_seconds)
        if self.poll_interval <= 0 or self.wait_timeout <= 0:
            raise ValueError('轮询间隔和等待上限必须大于零')
        self.clock, self.sleep, self.now = clock or time.monotonic, sleep or time.sleep, now or timestamp
        self.started, self.started_at = self.clock(), self.now()
        self._owns_transport = transport is None
        if transport is None:
            import requests
            transport = requests.Session()
            transport.trust_env = False
        self.transport = RecordingTransport(transport)
        self.client = Client(self.settings, transport=self.transport, action='标准章批量评测')
        self.client._validate_credentials()
        self.output.mkdir(parents=True, exist_ok=True)
        body = json.loads(self.manifest.read_text(encoding='utf-8'))
        records = body.get('records') if isinstance(body, dict) else body
        if not isinstance(records, list):
            raise ValueError('manifest 必须包含 records 列表')
        if limit is not None:
            records = records[:int(limit)]
        identifiers = [str(record.get('standard_id', record.get('id', index + 1))) for index, record in enumerate(records)]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('manifest 标准章 ID 重复')
        self.entries = [self._load(record, identifier) for record, identifier in zip(records, identifiers)]
        self.expired = set()
        self._progress()

    def _load(self, record, identifier):
        if not isinstance(record, dict) or not record.get('image_path'):
            raise ValueError('manifest 记录缺少 image_path')
        source = Path(record['image_path']).expanduser()
        source = source.resolve() if source.is_absolute() else (self.manifest.parent / source).resolve()
        safe_id = identifier if identifier.isdigit() else hashlib.sha256(identifier.encode()).hexdigest()[:16]
        path = self.output / f'standard-{int(safe_id):04d}.json' if safe_id.isdigit() else self.output / f'standard-{safe_id}.json'
        digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else ''
        environment = sanitized(identity(self.settings), self.settings)
        if path.exists():
            report = json.loads(path.read_text(encoding='utf-8'))
            if (report.get('environment') != environment or report.get('input_path') != str(source)
                    or report.get('input_sha256') != digest or report.get('standard_id') != identifier):
                raise ValueError(f'图片、账号或场景与已有报告不一致，请使用原配置或新的输出目录：{path}')
            if report.get('req_uuid') and report['status'] not in TERMINAL:
                report['status'] = 'waiting'
            elif report['status'] == 'submitting':
                report.update(status='unknown', error='上次提交的受理状态待确认，禁止自动重发')
            elif report['status'] == 'uploading':
                report['status'] = 'queued'  # No recognition POST was started.
        else:
            report = {'schema_version': 1, 'standard_id': identifier, 'record': deepcopy(record),
                      'input_path': str(source), 'input_sha256': digest, 'environment': environment,
                      'model_id': self.settings.model_id, 'settings_source': self.settings_source,
                      'status': 'queued', 'created_at': self.now(), 'req_uuid': '',
                      'requests': [], 'runs': [], 'retry_9007_count': 0}
        if not source.is_file() or (record.get('image_sha256') and record['image_sha256'] != digest):
            report.update(status='failed', error='原始标准图片缺失或 SHA256 与 manifest 不一致')
        report['runs'].append({'started_at': self.started_at})
        entry = {'report': report, 'path': path, 'source': source,
                 'due': self.clock(), 'deadline': self.clock() + self.wait_timeout,
                 'request_offset': len(report['requests'])}
        self._save(entry)
        return entry

    def _save(self, entry):
        report = entry['report']
        update_totals(report)
        report['timings']['ocr_calls'] = int(bool(report.get('req_uuid')))
        report['timings']['submission_attempts'] = sum(event['phase'] == 'submit' for event in report['requests'])
        if report['requests']:
            end = (report.get('completed_at') or
                   report['requests'][-1].get('finished_at') if report['status'] in TERMINAL else self.now())
            report['timings']['elapsed_seconds'] = round((datetime.fromisoformat(end) -
                datetime.fromisoformat(report['requests'][0]['started_at'])).total_seconds(), 6) if end else None
        else:
            report['timings']['elapsed_seconds'] = None
        report['response'] = {'status': report['status'], 'req_uuid': report.get('req_uuid', ''),
                              'commitResult': deepcopy(report.get('commitResult'))}
        write_json(entry['path'], report, self.settings)

    def _progress(self):
        counts = {key: 0 for key in ('completed', 'waiting', 'failed', 'unknown', 'queued')}
        for entry in self.entries:
            status = entry['report']['status']
            counts[status if status in counts else 'queued'] += 1
        counts['total'] = len(self.entries)
        progress = {'model_id': self.settings.model_id, 'manifest': str(self.manifest),
                    'started_at': self.started_at, 'updated_at': self.now(),
                    'elapsed_seconds': round(self.clock() - self.started, 6), 'counts': counts,
                    'max_in_flight': self.max_in_flight,
                    'records': [{'standard_id': entry['report']['standard_id'], 'status': entry['report']['status'],
                                 'req_uuid': entry['report'].get('req_uuid', ''), 'report': str(entry['path'])}
                                for entry in self.entries]}
        write_json(self.output / 'progress.json', progress, self.settings)
        return progress

    def _phase(self, entry, phase, operation):
        report = entry['report']
        event = {'phase': phase, 'started_at': self.now(), 'status': 'running'}
        report['requests'].append(event)
        if 'run_started' not in entry:
            entry['run_started'] = self.clock()
            report['runs'][-1]['request_started_at'] = self.now()
        started = self.clock()
        try:
            # The adapter owns the physical HTTP mutex. Acquiring it here as
            # well would deadlock a real Client; the batch loop is serial.
            value = operation()
            event['status'] = 'succeeded'
            return value
        except BaseException as exc:
            event.update(status='failed', error=safe_error_text(exc, self.settings))
            raise
        finally:
            event.update(seconds=round(max(0, self.clock() - started), 6), finished_at=self.now())
            entry['last_request_finish'] = self.clock()
            if self.transport.last_response is not None:
                event['response'] = deepcopy(self.transport.last_response)

    def _is_transfer_rejection(self):
        response = self.transport.last_response or {}
        body = response.get('body') or {}
        return (isinstance(body, dict) and body.get('code') == 9007
                and '转储' in json.dumps(body, ensure_ascii=False))

    def _submit(self, entry):
        report = entry['report']
        while True:
            if report['status'] == 'retrying':
                retry_at = datetime.fromisoformat(report['retry_at'])
                remaining = (retry_at - datetime.fromisoformat(self.now())).total_seconds()
                if remaining > 0:
                    self.sleep(remaining)
            report['status'] = 'submitting'
            report['request_parameters'] = self.client.build_request(entry['source'].name, report['file_id'])
            self._save(entry)  # A crash after this point must not repeat the POST.
            self.transport.last_response = None
            try:
                body = self._phase(entry, 'submit', lambda: self.client.submit(entry['source'].name, report['file_id']))
            except Exception as exc:
                error = safe_error_text(exc, self.settings)
                if getattr(exc, 'provider_rejected', False) and self._is_transfer_rejection() and report['retry_9007_count'] < 2:
                    report.update(status='retrying', error=error,
                                  retry_9007_count=report['retry_9007_count'] + 1,
                                  retry_at=(datetime.fromisoformat(self.now()) + timedelta(seconds=3)).isoformat())
                    self._save(entry)
                    continue
                report.update(status='failed' if getattr(exc, 'provider_rejected', False) else 'unknown', error=error)
                self._save(entry)
                return
            report.update(status='waiting', req_uuid=body['data']['reqUuid'], submission=body,
                          submitted_at=self.now())
            report.pop('error', None)
            entry.update(due=self.clock() + self.poll_interval, deadline=self.clock() + self.wait_timeout)
            try:
                self._save(entry)  # UUID is durable before any GET or next input.
            except BaseException:
                print(f"受理编号尚未落盘，请保留并核对：{report['req_uuid']}", file=sys.stderr)
                raise
            return

    def _start(self, entry):
        report = entry['report']
        if report['status'] in {'uploaded', 'retrying'}:
            self._submit(entry)
            return
        report['status'] = 'uploading'
        self._save(entry)
        self.transport.last_response = None
        try:
            uploaded = self._phase(entry, 'upload', lambda: self.client.upload_file(entry['source']))
        except Exception as exc:
            report.update(status='failed', error=safe_error_text(exc, self.settings))
            self._save(entry)
            return
        report.update(status='uploaded', file_id=uploaded['fileId'], upload=uploaded['trace'])
        self._save(entry)
        self._submit(entry)

    def _poll(self, entry):
        report = entry['report']
        self.transport.last_response = None
        try:
            remaining = max(.1, entry['deadline'] - self.clock())
            from urllib3.util import Timeout
            body = self._phase(entry, 'query', lambda: self.client.get_result(report['req_uuid'],
                timeout=Timeout(total=remaining, connect=min(5, remaining), read=min(15, remaining))))
        except Exception as exc:
            response = self.transport.last_response or {}
            report.update(error=safe_error_text(exc, self.settings))
            if getattr(exc, 'provider_rejected', False) and (response.get('http_status') or 200) < 500:
                report['status'] = 'failed'
            entry['due'] = self.clock() + self.poll_interval
            self._save(entry)
            return
        report['last_response'] = body
        data = body.get('data')
        commit = data.get('commitResult') if isinstance(data, dict) else None
        if not isinstance(data, dict) or (commit is not None and not isinstance(commit, dict)):
            report.update(status='failed', error='单证通查询返回的 data/commitResult 格式错误')
        elif commit:
            report.update(status='completed', result=deepcopy(data), commitResult=deepcopy(commit), completed_at=self.now())
            report.pop('error', None)
            report.pop('wait_timeout', None)
            report.pop('next_action', None)
        else:
            entry['due'] = self.clock() + self.poll_interval
            report.pop('error', None)
        self._save(entry)

    def run(self):
        try:
            while True:
                waiting = [entry for entry in self.entries if entry['report']['status'] == 'waiting'
                           and entry['report']['standard_id'] not in self.expired]
                candidates = [entry for entry in self.entries if entry['report']['status'] in {'queued', 'uploaded', 'retrying'}]
                while candidates and len(waiting) < self.max_in_flight:
                    entry = candidates.pop(0)
                    self._start(entry)
                    self._progress()
                    waiting = [item for item in self.entries if item['report']['status'] == 'waiting'
                               and item['report']['standard_id'] not in self.expired]
                for entry in waiting:
                    if self.clock() >= entry['deadline']:
                        self.expired.add(entry['report']['standard_id'])
                        entry['report']['wait_timeout'] = True
                        entry['report']['next_action'] = '仍待返回；同命令再次运行仅续查原 UUID'
                        self._save(entry)
                active = [entry for entry in waiting if entry['report']['standard_id'] not in self.expired]
                if not active:
                    if any(entry['report']['status'] in {'queued', 'uploaded', 'retrying'} for entry in self.entries):
                        continue  # Timed-out UUIDs remain durable; later inputs still run.
                    break
                due = min(active, key=lambda item: item['due'])
                if due['due'] > self.clock():
                    self.sleep(min(due['due'] - self.clock(), max(.01, due['deadline'] - self.clock())))
                    continue
                self._poll(due)
                self._progress()
        finally:
            for entry in self.entries:
                active_elapsed = (entry['last_request_finish'] - entry['run_started']
                                  if 'run_started' in entry else 0.0)
                request_count = len(entry['report']['requests']) - entry['request_offset']
                finished_at = (entry['report']['requests'][-1].get('finished_at') if request_count else self.started_at)
                entry['report']['runs'][-1].update(finished_at=finished_at,
                    elapsed_seconds=round(active_elapsed, 6), elapsed_scope='first_to_last_http_in_this_run',
                    request_count=request_count)
                self._save(entry)
            if self._owns_transport:
                self.transport.transport.close()
        return self._progress()


def run_benchmark(manifest, output, *, settings=None, database=DATABASE, **kwargs):
    loaded, source = load_settings(database) if settings is None else (seal_settings(settings), 'injected')
    return Benchmark(manifest, output, loaded, source, **kwargs).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-in-flight', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--poll-interval-seconds', type=float, default=2)
    parser.add_argument('--wait-timeout-seconds', type=float, default=600)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args(argv)
    settings = None
    try:
        settings, settings_source = load_settings()
        result = Benchmark(args.manifest, args.output, settings, settings_source,
                           max_in_flight=args.max_in_flight, poll_interval_seconds=args.poll_interval_seconds,
                           wait_timeout_seconds=args.wait_timeout_seconds, limit=args.limit).run()
        print(json.dumps(result['counts'], ensure_ascii=False))
        counts = result['counts']
        return 1 if counts['failed'] or counts['unknown'] else 2 if counts['waiting'] or counts['queued'] else 0
    except KeyboardInterrupt:
        print('已停止；已受理请求将沿原 UUID 续查，未知提交不会重发。', file=sys.stderr)
        return 130
    except Exception as exc:
        print(safe_error_text(exc, settings), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
