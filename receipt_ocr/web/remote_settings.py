"""Local configuration of the remote document provider, without exposing secrets."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from urllib.parse import urlsplit

from flask import jsonify, request
from werkzeug.utils import secure_filename

from receipt_ocr import danzhengtong as dzt
from receipt_ocr.danzhengtong_errors import safe_error_text

SECRETS = {'app_key', 'app_secret', 'key_id'}
REQUIRED = ('app_id', 'app_key', 'app_secret', 'key_id', 'callback_url', 'org_id', 'sys_code', 'model_id', 'base_url', 'upload_url')
_lock = RLock()


def restore(database):
    saved = database.get_json_setting('danzhengtong_credentials', None)
    dzt.set_software_settings(dzt.Settings(**saved) if isinstance(saved, dict) else None)


def status(database):
    settings = dzt.Settings.load()
    values = asdict(settings)
    missing = [key for key in REQUIRED if not values[key]]
    configured = not missing
    tested = database.get_json_setting('danzhengtong_tested', False)
    label = '测试通过' if configured and tested else '已配置' if configured else '配置不完整' if any(values[k] for k in ('app_id', *SECRETS)) else '未配置'
    return {'configured': configured, 'status': label, 'missing': missing,
            'values': {k: v for k, v in values.items() if k not in SECRETS},
            'secrets_set': {k: bool(values[k]) for k in SECRETS}}


def same_origin():
    origin = request.headers.get('Origin')
    return not origin or origin == request.host_url.rstrip('/')


def settings_handler(database):
    if request.method == 'GET':
        return jsonify(status(database))
    if not same_origin():
        return jsonify(error='请在软件设置页面保存配置'), 403
    payload = request.get_json(silent=True)
    if not request.is_json or not isinstance(payload, dict):
        return jsonify(error='配置必须是 JSON 对象'), 400
    with _lock:
        values = asdict(dzt.Settings.load())
        if payload.get('clear') is True:
            values = asdict(dzt.Settings())
        else:
            if set(payload) - set(values):
                return jsonify(error='包含未知配置项'), 400
            for key, value in payload.items():
                if key == 'poll_timeout_seconds':
                    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
                        return jsonify(error='查询超时应为 1 至 60 秒的整数'), 400
                else:
                    if not isinstance(value, str) or len(value) > 4096:
                        return jsonify(error='配置内容无效'), 400
                    value = value.strip()
                    if key in SECRETS and not value:
                        continue  # Blank secret fields preserve saved values.
                    if any(c.isspace() for c in value):
                        return jsonify(error='配置不能包含空白字符'), 400
                    if key in {'base_url', 'upload_url', 'callback_url'} and value:
                        try:
                            url = urlsplit(value)
                            valid_url = (url.scheme in {'http', 'https'} and bool(url.hostname)
                                         and not url.username and not url.password)
                            url.port  # Validate malformed ports as well.
                        except ValueError:
                            valid_url = False
                        if not valid_url:
                            return jsonify(error='请输入有效的 HTTP 或 HTTPS 地址'), 400
                values[key] = value
        database.set_json_setting('danzhengtong_credentials', values)
        database.set_json_setting('danzhengtong_tested', False)
        dzt.set_software_settings(dzt.Settings(**values))
        return jsonify(status(database))


def test_handler(database, allowed_extensions):
    if not same_origin():
        return jsonify(error='请在软件设置页面发起测试'), 403
    settings = dzt.Settings.load()  # Snapshot stays unchanged throughout upload and polling.
    if not status(database)['configured']:
        return jsonify(error='请先补齐并保存单证通配置'), 400
    file = request.files.get('file')
    if not file or Path(file.filename or '').suffix.lower() not in allowed_extensions:
        return jsonify(error='请选择 JPG、PNG、BMP 或 WebP 回单图片'), 400
    suffix = Path(file.filename).suffix.lower()
    name = (secure_filename(Path(file.filename).stem) or 'receipt') + suffix
    try:
        with TemporaryDirectory(prefix='dzt-test-') as directory:
            path = Path(directory) / name
            file.save(path)
            dzt.Client(settings=settings).recognize(path, file_name=name)
        with _lock:
            if dzt.Settings.load() != settings:
                return jsonify(error='测试期间配置已更改，请重新测试'), 409
            database.set_json_setting('danzhengtong_tested', True)
        return jsonify({**status(database), 'message': '测试通过：上传、提交和查询均成功'})
    except Exception as exc:
        with _lock:
            if dzt.Settings.load() == settings:
                database.set_json_setting('danzhengtong_tested', False)
        return jsonify(error=safe_error_text(exc, settings)), 502
