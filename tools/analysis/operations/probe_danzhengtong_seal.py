"""Probe the independent seal scene without changing application state.

Example::

    python -m tools.analysis.operations.probe_danzhengtong_seal \
        --input /path/to/seal-0-enhanced.png --output outputs/seal-probe

Reusing the output directory resumes accepted UUIDs instead of uploading or
submitting again. A submission interrupted before its UUID was saved requires
manual investigation; this tool never retries an uncertain submission.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from receipt_ocr.danzhengtong import Client, Settings
from receipt_ocr.danzhengtong_errors import safe_error_text

MODEL_ID = 'logistics_samsung_outseal_1503'
DATABASE = PROJECT_ROOT / 'storage' / 'results.db'
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
SECRET_KEYS = {'appid', 'appkey', 'appsecret', 'keyid', 'authorization',
               'password', 'token', 'signature', 'sign'}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def load_settings(database=DATABASE):
    """Read software credentials without creating or migrating its database."""
    path = Path(database).resolve()
    if path.is_file():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'"
            ).fetchone()
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key='danzhengtong_credentials'"
            ).fetchone() if table else None
        if row:
            saved = json.loads(row[0])
            if not isinstance(saved, dict):
                raise ValueError('软件中保存的单证通配置格式错误')
            return replace(Settings(**saved), model_id=MODEL_ID), 'software_database'
    return replace(Settings.load(), model_id=MODEL_ID), 'environment_or_local_file'


def identity(settings):
    return {**{key: getattr(settings, key) for key in
               ('base_url', 'upload_url', 'upload_source_code', 'sys_code', 'org_id', 'model_id')},
            'account_sha256': hashlib.sha256(settings.app_id.encode()).hexdigest()}


def sanitized(value, settings):
    """Keep provider data intact except credentials and URL query parameters."""
    if isinstance(value, dict):
        return {key: ('***' if re.sub(r'[^a-z]', '', key.lower()) in SECRET_KEYS
                      else sanitized(item, settings))
                for key, item in value.items() if key.lower() != 'filepath'}
    if isinstance(value, list):
        return [sanitized(item, settings) for item in value]
    if isinstance(value, str):
        for secret in sorted((getattr(settings, key) for key in
                              ('app_id', 'app_key', 'app_secret', 'key_id')
                              if getattr(settings, key)), key=len, reverse=True):
            value = value.replace(secret, '***')
        return re.sub(r'https?://[^\s\'"<>]+',
                      lambda match: match[0].split('?')[0].split('#')[0], value)
    return value


def write_json(path, value, settings):
    """Replace complete reports atomically so accepted UUIDs survive restarts."""
    destination = Path(path)
    temporary = destination.with_name(destination.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(sanitized(value, settings), stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)


def report_path(output, source):
    name = re.sub(r'[^\w.-]+', '-', source.stem, flags=re.UNICODE)[:70] or 'seal'
    path_hash = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    return Path(output) / f'{name}-{path_hash}.json'


def record_request(report, phase, request):
    """Measure actual transport calls; never include poll sleeps as requests."""
    event = {'phase': phase, 'started_at': timestamp(), 'status': 'running'}
    report['requests'].append(event)
    started = time.perf_counter()
    try:
        # Client serializes physical HTTP. Do not acquire its non-reentrant
        # mutex again around the call.
        value = request()
        event['status'] = 'succeeded'
        return value
    except BaseException:
        event['status'] = 'failed'
        raise
    finally:
        event.update(finished_at=timestamp(), seconds=round(time.perf_counter() - started, 6))


def update_totals(report):
    measured = [event for event in report['requests'] if 'seconds' in event]
    report['timings'] = {
        **{f'{phase}_seconds': round(sum(event['seconds'] for event in measured
                                       if event['phase'] == phase), 6)
           for phase in ('upload', 'submit', 'query')},
        'query_count': sum(event['phase'] == 'query' for event in measured),
        'request_seconds': round(sum(event['seconds'] for event in measured), 6),
    }
    report['updated_at'] = timestamp()


def probe(source, output, settings, settings_source, timeout_seconds, *, retry_rejected=False):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f'请输入增强后的印章图片：{source}')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    path = report_path(output, source)
    environment = sanitized(identity(settings), settings)
    if path.exists():
        report = json.loads(path.read_text(encoding='utf-8'))
        if (report.get('input_sha256') != digest or report.get('model_id') != MODEL_ID
                or report.get('input_path') != str(source)):
            raise ValueError(f'输入图片或场景与已有报告不一致，请核对：{path}')
        if report.get('environment') != environment:
            raise ValueError(f'账号或接口配置与已有报告不一致，请切回原配置：{path}')
        if report.get('status') == 'completed':
            return report, path
        if (retry_rejected and report.get('status') == 'submission_rejected'
                and report.get('file_id')):
            report['status'] = 'uploaded'
        if not report.get('req_uuid') and report.get('status') != 'uploaded':
            raise ValueError(f'已有请求尚无已保存的受理编号，禁止自动重发，请人工核对：{path}')
    else:
        report = {'schema_version': 1, 'input_path': str(source), 'input_sha256': digest,
                  'input_bytes': source.stat().st_size, 'model_id': MODEL_ID,
                  'environment': environment, 'settings_source': settings_source,
                  'status': 'new', 'created_at': timestamp(), 'req_uuid': '',
                  'requests': [], 'runs': []}

    client = Client(settings=settings)
    client._validate_credentials()
    run = {'started_at': timestamp()}
    report['runs'].append(run)
    run_started = time.perf_counter()

    def save():
        update_totals(report)
        write_json(path, report, settings)

    try:
        if not report.get('req_uuid'):
            if report['status'] != 'uploaded':
                report['status'] = 'uploading'
                save()
                try:
                    uploaded = record_request(report, 'upload', lambda: client.upload_file(source))
                except BaseException:
                    report['status'] = 'upload_failed'
                    raise
                report.update(status='uploaded', file_id=uploaded['fileId'],
                              upload=uploaded['trace'])
                save()
            # Persist uncertainty BEFORE POST. A crash here must not cause a
            # second accepted recognition request on the next invocation.
            report['status'] = 'submitting'
            save()
            try:
                body = record_request(report, 'submit',
                                      lambda: client.submit(source.name, report['file_id']))
            except BaseException as exc:
                report['status'] = 'submission_rejected' if getattr(exc, 'provider_rejected', False) else 'submission_unknown'
                raise
            report.update(req_uuid=body['data']['reqUuid'], status='waiting',
                          submitted_at=timestamp(), submission=body)
            # No GET or normalization before the accepted UUID is durable.
            try:
                save()
            except BaseException:
                print(f"受理编号尚未成功落盘，请保留并人工核对：{report['req_uuid']}", file=sys.stderr)
                raise

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report.update(status='waiting', error='本次轮询等待已到上限；再次运行同命令仅继续查询原受理编号')
                break
            time.sleep(min(2, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                continue
            try:
                from urllib3.util import Timeout
                body = record_request(report, 'query', lambda: client.get_result(
                    report['req_uuid'], timeout=Timeout(total=remaining, connect=min(5, remaining), read=min(15, remaining))))
                data = body.get('data')
                if not isinstance(data, dict):
                    raise ValueError('单证通查询返回的 data 格式错误')
                commit = data.get('commitResult')
                if commit is not None and not isinstance(commit, dict):
                    raise ValueError('单证通查询返回的 commitResult 格式错误')
                report['last_response'] = body
                report.pop('error', None)
                if commit:
                    report.update(status='completed', result=data, commitResult=commit,
                                  completed_at=timestamp())
                    save()
                    break
                report['status'] = 'waiting'
            except Exception as exc:
                # Query failures never discard the accepted UUID and never
                # trigger a new upload or recognition submission.
                report.update(status='waiting', error=safe_error_text(exc, settings))
                report.setdefault('query_errors', []).append(
                    {'at': timestamp(), 'error': safe_error_text(exc, settings)})
            save()
    except BaseException as exc:
        report['error'] = safe_error_text(exc, settings)
        if report.get('req_uuid'):
            report['status'] = 'waiting'
        raise
    finally:
        run.update(finished_at=timestamp(), elapsed_seconds=round(time.perf_counter() - run_started, 6))
        save()
    return report, path


def positive_seconds(value):
    seconds = int(value)
    if seconds < 1:
        raise argparse.ArgumentTypeError('等待秒数必须大于 0')
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', required=True, type=Path, help='一个或多个加强后的完整印章图片')
    parser.add_argument('--output', required=True, type=Path, help='保存报告并支持续查的目录')
    parser.add_argument('--poll-timeout-seconds', type=positive_seconds, default=300)
    parser.add_argument('--retry-rejected', action='store_true',
                        help='仅对接口明确拒绝且未受理的提交，复用已上传 fileId 再提交一次；未知状态仍不重发')
    args = parser.parse_args(argv)
    settings = None
    try:
        settings, settings_source = load_settings()
        args.output.mkdir(parents=True, exist_ok=True)
        results, failures = [], []
        for source in dict.fromkeys(args.input):
            try:
                report, path = probe(source, args.output, settings, settings_source, args.poll_timeout_seconds,
                                     retry_rejected=args.retry_rejected)
                results.append({'input_path': str(source.resolve()), 'status': report['status'],
                                'report': str(path.resolve()), 'req_uuid': report.get('req_uuid', '')})
                print(f"{source.name}: {report['status']} → {path}")
            except Exception as exc:
                message = safe_error_text(exc, settings)
                failures.append({'input_path': str(source.resolve()), 'error': message})
                print(f'{source.name}: {message}', file=sys.stderr)
        summary = {'model_id': MODEL_ID, 'updated_at': timestamp(), 'results': results, 'failures': failures}
        write_json(args.output / 'summary.json', summary, settings)
        return 1 if failures else 2 if any(item['status'] != 'completed' for item in results) else 0
    except KeyboardInterrupt:
        print('已停止；已有受理编号保存在报告中，重跑同命令可继续查询。', file=sys.stderr)
        return 130
    except Exception as exc:
        print(safe_error_text(exc, settings), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
