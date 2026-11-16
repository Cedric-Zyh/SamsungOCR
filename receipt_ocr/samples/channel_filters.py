"""Read-only date and receiver summaries for independent sample source filters."""
from receipt_ocr.domain.fields.schema import derive_signature_check


RECOGNITION_SOURCES = [
    {'key': 'combined', 'label': '综合结果'},
    {'key': 'local:paddle_v6', 'label': '本地 · PP-OCRv6'},
    {'key': 'danzhengtong', 'label': '单证通'},
    {'key': 'local:legacy_local', 'label': '本地 · 历史结果'},
]
_PROVIDERS = ('paddle_v6', 'danzhengtong', 'legacy_local')
_WAITING = {'pending', 'waiting', 'running', 'queued', 'uploading', 'submitting',
            'submitted', 'polling', '测试中', '识别中', '待识别'}
_FAILED = {'failed', 'error', '识别失败', '执行失败', '运行失败'}
_SKIPPED = {'skipped', 'cancelled', 'canceled', 'not_run', '未执行', '未运行', '已跳过'}


def _object(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _variants(result, stage):
    return {value['method']: value for value in (_object(result.get('recognition_variants')).get(stage) or [])
            if isinstance(value, dict) and isinstance(value.get('method'), str)}


def source_availability(job, enabled, *evidence):
    if not job.get('status'):
        return '未测试'
    if enabled is False:
        return '未运行'
    if job['status'] in {'queued', 'running', 'awaiting_upload'}:
        return '测试中'
    for value in evidence:
        value = _object(value)
        states = {str(value.get(key) or '').lower() for key in ('status', 'execution_status')}
        if value.get('error') or states & _FAILED:
            return '调用失败'
        if value.get('pending') or states & _WAITING:
            return '测试中'
        if states & _SKIPPED:
            return '未运行'
        if value.get('simulated') or value.get('mode') in {'mock', 'simulated', 'demo'}:
            return '缺少结果'
    return ''


def _key(method):
    return method if method == 'danzhengtong' else 'local:' + method


def _methods(config, stage, variants):
    declared = _object(config).get(stage)
    return set(declared) if isinstance(declared, list) else set(variants)


def _date_observations(result, config):
    variants = _variants(result, 'date')
    methods = _methods(config, 'date', variants)
    if not variants:
        check = _object(result.get('date_check'))
        if '人工' in str(check.get('source') or ''):
            return variants, methods
        source = str(check.get('backend') or check.get('source') or '')
        method = next(iter(methods)) if len(methods) == 1 else None
        if not method and '单证通' in source:
            method = 'danzhengtong'
        elif not method and ('本地' in source or source == '日期区域识别'):
            local = methods - {'danzhengtong'}
            method = next(iter(local)) if len(local) == 1 else 'legacy_local'
        if method and check:
            variants[method] = {'method': method, 'details': {'date_check': check}}
            if not isinstance(_object(config).get('date'), list):
                methods.add(method)
    return variants, methods


def date_source_filters(result, job, config, combined, make_cell):
    variants, methods = _date_observations(result, config)
    cells = {'combined': combined}
    for method in _PROVIDERS:
        variant = variants.get(method) or {}
        details = _object(variant.get('details'))
        check = _object(details.get('date_check'))
        state = source_availability(job, method in methods, variant, details, details.get('danzhengtong'), check)
        cell = make_cell(check, check.get('actual'), kind='date', enabled=method in methods)
        if state:
            cell.update(state=state, value='', recognized=False, accuracy='未评估')
        cells[_key(method)] = cell
    return cells


def _receiver_check(result, variant):
    details = _object(variant.get('details'))
    handwritten = _object(details.get('handwriting_fields'))
    value = _object(variant.get('value'))
    fields = _object(details.get('fields'))
    observed = variant.get('_observed') if '_observed' in variant else (
        isinstance(details.get('handwriting_fields'), dict)
        or isinstance(variant.get('value'), dict) or '仓库接收人' in fields)
    if not observed:
        return None
    receiver = next((_text(source.get('仓库接收人')) for source in (handwritten, value, fields)
                     if source.get('仓库接收人') is not None), '')
    contact = fields.get('仓库联系人')
    if contact is None:
        contact = _object(result.get('fields')).get('仓库联系人')
    # The receiver must belong to this provider, including a real empty read.
    return derive_signature_check({'仓库联系人': contact, '仓库接收人': receiver})


def signature_source_filters(result, job, config):
    fields = _object(result.get('fields'))
    combined = derive_signature_check(fields, result.get('signature_check'))
    variants = _variants(result, 'handwriting')
    methods = _methods(config, 'handwriting', variants)
    declared = _object(config).get('handwriting')
    if (not variants and len(methods) == 1 and combined.get('source') != '人工复核'
            and (fields.get('仓库接收人') or result.get('signature_check'))):
        method = next(iter(methods))
        variants[method] = {'method': method, 'details': {'handwriting_fields': {
            '仓库接收人': fields.get('仓库接收人') or ''}}}

    def cell(check, enabled=None, evidence=()):
        state = source_availability(job, enabled, *evidence)
        if not state:
            state = (check or {}).get('status') or ('调用失败' if job.get('status') == 'failed' else '缺少结果')
        value = _text((check or {}).get('receiver')) if state not in {
            '未测试', '未运行', '测试中', '调用失败', '缺少结果'} else ''
        return {'state': state, 'value': value, 'recognized': bool(value), 'accuracy': '未评估'}

    cells = {'combined': cell(combined, bool(declared) if isinstance(declared, list) else None)}
    for method in _PROVIDERS:
        variant = variants.get(method) or {}
        details = _object(variant.get('details'))
        metadata = _object(_object(details.get('handwriting_metadata')).get('仓库接收人'))
        cells[_key(method)] = cell(_receiver_check(result, variant), method in methods,
            (variant, details, details.get('danzhengtong'), details.get('handwriting_check'), metadata))
    return cells
