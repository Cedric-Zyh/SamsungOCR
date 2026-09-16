"""Single-document async gateway adapter. Mock mode never opens a connection."""
from copy import deepcopy
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import time
import threading
import uuid

from .danzhengtong_errors import provider_error, safe_error_text
from .recognition_progress import report_dzt_progress, recognition_paused

ROOT = Path(__file__).resolve().parents[1]
_SINGLE_DOCUMENT = threading.Lock()

MOCK_NOTICE = '单证通模拟数据，仅用于接入调试，不代表当前单据的真实内容'


@dataclass(frozen=True, repr=False)
class Settings:
    mode: str = 'real'
    base_url: str = 'https://api.sinotrans.com'
    upload_url: str = 'http://techfile.i.sinotrans.com:80/objectstorecloud/files/v2'
    upload_source_code: str = ''
    app_id: str = ''
    app_key: str = ''
    app_secret: str = ''
    key_id: str = ''
    callback_url: str = ''
    sys_code: str = 'LOGISTICS_SAMSUNG'
    org_id: str = '101517'
    model_id: str = 'logistics_samsung_deliverynote_1503'
    poll_timeout_seconds: int = 60

    @classmethod
    def load(cls):
        path = ROOT / 'config' / 'danzhengtong.local.json'
        local = json.loads(path.read_text()) if path.exists() else {}
        return cls(**{key: os.getenv('DZT_' + key.upper(), local.get(key, field.default))
                      for key, field in cls.__dataclass_fields__.items()})


class Client:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or Settings.load()
        if self.settings.mode not in {'mock', 'real'}:
            raise ValueError('DZT_MODE 只能是 mock 或 real')
        self.transport = transport
        self.jobs = {}
        self.poll_timeout = min(60, int(self.settings.poll_timeout_seconds))
        if self.poll_timeout <= 0:
            raise ValueError('单证通查询超时必须大于 0 秒')

    def upload_file(self, source, *, file_name=None):
        """POST multipart to the file server; OCR credentials are never sent here."""
        report_dzt_progress('uploading', simulated=self.settings.mode == 'mock')
        path = Path(source)
        name = Path(file_name or path.name).name
        extension = path.suffix.lstrip('.').lower()
        s = self.settings
        form = {'org_id': s.org_id, 'source_code': s.upload_source_code or s.sys_code,
                'file_type': extension, 'file_name': name, 'file_is_outer_visible': 'N'}
        if not all(form[key] for key in ('org_id', 'source_code', 'file_type', 'file_name')):
            raise ValueError('上传文件缺少组织、系统编码、文件名或后缀')
        response = None
        if s.mode == 'mock':
            file_id = 'mock-file-' + uuid.uuid4().hex
            body = {'status': True, 'data': file_id, 'fileId': file_id, 'message': '模拟：文件上传成功'}
        else:
            if not s.upload_url:
                raise ValueError('单证通文件上传地址未配置')
            if not path.is_file():
                raise ValueError('待上传文件不存在或不是普通文件')
            if self.transport is None:
                import requests
                self.transport = requests.Session()
                self.transport.trust_env = False
            try:
                with path.open('rb') as stream:
                    # requests generates the multipart boundary. Do not set JSON headers.
                    response = self.transport.request(
                        'POST', s.upload_url, data=form,
                        files={'file': (name, stream, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')},
                        timeout=60)
                    response.raise_for_status()
                    body = response.json()
            except Exception as exc:
                raise provider_error('文件上传', s, exception=exc, response=response) from None
        if not isinstance(body, dict) or body.get('status') is not True:
            raise provider_error('文件上传', s, response=response, body=body, detail='接口返回上传失败或格式错误')
        file_id = body.get('fileId') or body.get('data')
        if not isinstance(file_id, str) or not file_id.strip():
            raise provider_error('文件上传', s, response=response, body=body, detail='上传响应缺少有效 fileId')
        # filePath can contain a signed download URL; never persist it in traces.
        return {'fileId': file_id, 'fileName': name,
                'trace': {'simulated': s.mode == 'mock', 'url': s.upload_url,
                          'form': form, 'response': {'status': True, 'fileId': file_id}}}

    def submit_file(self, source, *, file_name=None):
        if self.settings.mode == 'real':
            self._validate_credentials()  # Fail before uploading if submission cannot run.
        uploaded = self.upload_file(source, file_name=file_name)
        body = self.submit(uploaded['fileName'], uploaded['fileId'])
        self.jobs[body['data']['reqUuid']]['upload'] = uploaded['trace']
        return body

    def _validate_credentials(self):
        missing = [key for key in ('key_id', 'app_id', 'app_key', 'app_secret', 'callback_url', 'org_id', 'sys_code') if not getattr(self.settings, key)]
        if missing:
            raise ValueError('单证通配置缺失：' + ', '.join(missing))

    def build_request(self, file_name, file_id):
        s = self.settings
        if not file_name or not file_id:
            raise ValueError('必须提供 fileName 和上传平台返回的 fileId')
        return {
            'files': [{'fileName': file_name, 'fileId': file_id}],
            'docType': 'SINGLE_LLM_EXTRACT_ASYNC',
            'appId': s.app_id, 'appKey': s.app_key, 'appSecret': s.app_secret,
            'callBackUrl': s.callback_url or ('https://callback.example.com/ocr/samsung/notify' if s.mode == 'mock' else ''),
            'modelId': s.model_id, 'sysCode': s.sys_code, 'orgId': s.org_id,
        }

    def _request(self, method, path, **kwargs):
        s = self.settings
        if s.mode != 'real':
            raise RuntimeError('模拟模式禁止网络请求')
        self._validate_credentials()
        if self.transport is None:
            import requests
            self.transport = requests.Session()
            self.transport.trust_env = False
        stage = '查询结果' if method == 'GET' else '提交识别'
        response = None
        # Keep the actual error while redacting credentials and signed URLs.
        try:
            response = self.transport.request(method, s.base_url.rstrip('/') + path,
                                              headers={'keyId': s.key_id, 'Content-Type': 'application/json'},
                                              timeout=kwargs.pop('timeout', 30), **kwargs)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise provider_error(stage, s, exception=exc, response=response) from None
        if not isinstance(body, dict) or body.get('code') != 200 or body.get('status') is False:
            raise provider_error(stage, s, response=response, body=body, detail='接口返回业务失败或未知响应格式')
        return body

    def submit(self, file_name, file_id):
        report_dzt_progress('submitting', simulated=self.settings.mode == 'mock')
        payload = self.build_request(file_name, file_id)
        if self.settings.mode == 'real':
            body = self._request('POST', '/ocr/general-async/v1/extractGeneralDataAsync', json=payload)
        else:
            body = {'code': 200, 'data': {'recordId': 'mock-' + uuid.uuid4().hex,
                    'reqUuid': 'mock-' + uuid.uuid4().hex}, 'message': '模拟：调用成功，请等待识别完成', 'status': True}
        data = body.get('data') or {}
        if body.get('status') is not True or not isinstance(data, dict) or not data.get('reqUuid'):
            raise provider_error('提交识别', self.settings, body=body, detail='受理失败或缺少 reqUuid')
        safe_payload = {k: ('***' if k in {'appId', 'appKey', 'appSecret'} else v) for k, v in payload.items()}
        self.jobs[data['reqUuid']] = {'request': safe_payload, 'submission': deepcopy(body), 'status': 'submitted'}
        return body

    def receive_callback(self, body):
        data = body.get('data') or {}
        job = self.jobs.get(data.get('reqUuid'))
        if job is None:
            raise ValueError('回调 reqUuid 未关联到当前任务')
        if body.get('code') != 200 or body.get('success') is not True or data.get('status') != 1:
            job['status'] = 'failed'
            raise ValueError(str(provider_error('回调识别', self.settings, body=body,
                                               detail=f"识别状态={data.get('status')}")))
        job.update(status='finished', callback=deepcopy(body))
        return data['reqUuid']

    def get_result(self, req_uuid, *, timeout=30):
        if self.settings.mode == 'real':
            return self._request('GET', '/ocr/async/v1/getResultByReqUuid', params={'reqUuid': req_uuid}, timeout=timeout)
        if self.jobs[req_uuid]['status'] == 'failed':
            raise RuntimeError('模拟任务识别失败，不能取结果')
        # This is OUR normalized fixture, not an assertion about the vendor schema.
        return json.loads((ROOT / 'config' / 'danzhengtong.mock.json').read_text())

    def query_after_delay(self, req_uuid):
        """Query once after two seconds, independently of callback delivery."""
        job = self.jobs[req_uuid]
        job['query'] = {'delay_seconds': 2, 'strategy': 'fixed_delay', 'status': 'waiting'}
        report_dzt_progress('waiting', simulated=self.settings.mode == 'mock', poll_count=0,
                            timeout_seconds=self.poll_timeout)
        time.sleep(2)
        try:
            result = self.get_result(req_uuid)
        except Exception:
            job['query']['status'] = 'failed'
            raise
        # A successful HTTP query alone does not establish recognition completion.
        job['query']['status'] = 'received'
        return result

    def recognize_mock(self, source, *, file_name=None):
        if self.settings.mode != 'mock':
            raise RuntimeError('真实整单识别待联调：需真实结果字段映射')
        submitted = self.submit_file(source, file_name=file_name)
        req_uuid = submitted['data']['reqUuid']
        result = self.query_after_delay(req_uuid)
        self.jobs[req_uuid]['status'] = 'completed'
        report_dzt_progress('completed', simulated=True)
        return result, {'mode': 'mock', 'simulated': True, 'reqUuid': req_uuid,
                        **deepcopy(self.jobs[req_uuid])}


    def recognize(self, source, *, file_name=None):
        # The existing queue is serial; also serialize direct/retry callers here.
        with _SINGLE_DOCUMENT:
            if self.settings.mode == 'mock':
                return self.recognize_mock(source, file_name=file_name)
            submitted = self.submit_file(source, file_name=file_name)
            req_uuid = submitted['data']['reqUuid']
            job = self.jobs[req_uuid]
            deadline = time.monotonic() + self.poll_timeout
            job['query'] = {'strategy': 'poll', 'interval_seconds': 2, 'attempts': 0, 'status': 'waiting'}
            report_dzt_progress('waiting', simulated=False, poll_count=0, timeout_seconds=self.poll_timeout)
            paused = False
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f'单证通等待识别结果超时（{self.poll_timeout} 秒），已停止查询')
                    if recognition_paused():
                        if not paused:
                            job['query']['status'] = 'paused'
                            report_dzt_progress('paused', simulated=False, poll_count=job['query']['attempts'],
                                                timeout_seconds=self.poll_timeout)
                        paused = True
                        time.sleep(min(.2, remaining))
                        continue
                    if paused:
                        paused = False
                        job['query']['status'] = 'waiting'
                        report_dzt_progress('waiting', simulated=False, poll_count=job['query']['attempts'],
                                            timeout_seconds=self.poll_timeout)
                    time.sleep(min(2, remaining))
                    if recognition_paused():
                        continue  # A pause during the delay must prevent the next GET.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        continue
                    from urllib3.util import Timeout
                    timeout = Timeout(total=remaining, connect=min(5, remaining), read=min(30, remaining))
                    job['query']['attempts'] += 1
                    response = self.get_result(req_uuid, timeout=timeout)
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'单证通等待识别结果超时（{self.poll_timeout} 秒），已停止查询')
                    data = response.get('data')
                    if not isinstance(data, dict):
                        raise provider_error('查询结果', self.settings, body=response, detail='data 格式错误')
                    commit = data.get('commitResult')
                    if commit is None:
                        report_dzt_progress('waiting', simulated=False, poll_count=job['query']['attempts'],
                                            timeout_seconds=self.poll_timeout)
                        continue
                    if not isinstance(commit, dict):
                        raise provider_error('查询结果', self.settings, body=response, detail='commitResult 格式错误')
                    # Empty objects provide no evidence that recognition is complete.
                    if not commit:
                        report_dzt_progress('waiting', simulated=False, poll_count=job['query']['attempts'],
                                            timeout_seconds=self.poll_timeout)
                        continue
                    normalized = normalize_result(commit)
                    job['query']['status'] = 'completed'
                    job['status'] = 'completed'
                    job['result'] = {'recordId': data.get('recordId'), 'templateId': data.get('templateId'),
                                     'templateName': data.get('templateName'), 'commitResult': deepcopy(commit)}
                    report_dzt_progress('completed', simulated=False)
                    return normalized, {'mode': 'real', 'simulated': False, 'reqUuid': req_uuid, **deepcopy(job)}
            except Exception as exc:
                job['status'] = 'failed'
                job['query']['status'] = 'failed'
                # Keep the accepted UUID available for manual queries; never resubmit here.
                raise RuntimeError(f'{exc}；reqUuid={req_uuid}') from None


def normalize_result(commit):
    """Map the verified single-scene response; preserve empty and numeric zero values."""
    from .field_schema import OUTPUT_FIELDS
    fields, metadata = {}, {}
    for name in (*OUTPUT_FIELDS, '收货客户印章'):
        item = commit.get(name)
        if item is None:
            item = {'value': ''}
        if not isinstance(item, dict):
            raise RuntimeError(f'单证通字段格式错误：{name}')
        value = item.get('value')
        if value is not None and not isinstance(value, (str, int, float)):
            raise RuntimeError(f'单证通字段值格式错误：{name}')
        fields[name] = '' if value is None else str(value)
        metadata[name] = {'value': fields[name], 'original': fields[name], 'source': '单证通',
                          'provider_ratio': item.get('ratio'), 'position': item.get('position') or [],
                          'simulated': False, 'low_confidence': False}
    return {'fields': fields, 'metadata': metadata, 'simulated': False}


def stage_result(context, stage, cache, previous_fields=None):
    from .pipeline import empty_result
    from .field_schema import PRINTED_FIELDS
    from .parser import compare_dates, parse_date
    from .parsing_seals import compare_seal_text_strict
    from .provider_field_policy import accept_real_dzt_fields
    if 'error' in cache:
        raise RuntimeError(cache['error'])
    if not cache:
        client = None
        try:
            client = Client()
            fixture, trace = client.recognize(context.source, file_name=context.filename)
            cache.update(fixture=fixture, trace=trace)
        except Exception as exc:
            cache['error'] = str(exc)
            settings = getattr(client, 'settings', None)
            report_dzt_progress('failed', simulated=getattr(settings, 'mode', 'real') == 'mock',
                                error_message=safe_error_text(exc, settings))
            raise
    fixture, trace = cache['fixture'], cache['trace']
    simulated = trace['simulated']
    if context.primary_backend is None and not context.document_type.get('provider_fields_accepted'):
        context.document_type['reasons'] = ['单证通模拟接入未验证文档类型' if simulated else '仅单证通识别，文档版式待复核']
    if stage == 'fields' and not simulated:
        context.document_type = accept_real_dzt_fields(context.document_type,
            fields={k: fixture['fields'][k] for k in PRINTED_FIELDS}, trace=trace)
    result = empty_result(context)
    def metadata(values):
        if not simulated:
            return {key: deepcopy(fixture['metadata'][key]) for key in values}
        return {key: {'value': value, 'original': value, 'source': '单证通（模拟）',
                      'low_confidence': True, 'simulated': True} for key, value in values.items()}
    if stage == 'fields':
        result['fields'] = {k: fixture['fields'][k] for k in PRINTED_FIELDS}
        result['field_metadata'] = metadata(result['fields'])
    elif stage == 'handwriting':
        result['handwriting_fields'] = {k: fixture['fields'][k] for k in ('仓库接收人', '实收数量', '拒收数量')}
        result['handwriting_metadata'] = metadata(result['handwriting_fields'])
    elif stage == 'products':
        if not simulated:
            raise ValueError('当前单证通场景未提供商品明细，请选择本地商品识别')
        result['product_table'] = deepcopy(fixture['product_table'])
    elif stage == 'date':
        raw_date = fixture['fields']['签收日期']
        result['date_check'] = compare_dates((previous_fields or {}).get('要求到货', ''), parse_date(raw_date))
        result['date_check'].update(reliable=not simulated and result['date_check']['status'] in {'匹配', '不匹配'},
                                   simulated=simulated, backend='单证通',
                                   source='单证通（模拟）' if simulated else '单证通', raw_text=raw_date,
                                   provider_ratio=fixture.get('metadata', {}).get('签收日期', {}).get('provider_ratio'))
    elif stage == 'seal':
        text = fixture['fields'].get('收货客户印章', '')
        result['seal_check'] = compare_seal_text_strict((previous_fields or {}).get('签章要求', ''), [text] if text else [])
        result['seal_check'].update(reliable=not simulated and result['seal_check']['reliable'],
                                   simulated=simulated, backend='单证通', recognition_mode='danzhengtong',
                                   source='单证通（模拟）' if simulated else '单证通')
    else:
        raise ValueError('单证通暂不支持该识别阶段')
    result['stage_review_reasons'] = [MOCK_NOTICE] if simulated else []
    result['danzhengtong'] = deepcopy(trace)
    return result
