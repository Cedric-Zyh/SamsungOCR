"""Compact, independent channel summaries for sample-library result filters.

Only scalar evidence is selected from stored JSON; images, OCR observations and
provider payloads never travel with a polling request for the sample grid.
"""
import json

from receipt_ocr.domain.parsing.parsing_dates import parse_date


SEAL_SOURCES = [
    {"key": "combined", "label": "综合结果"},
    {"key": "qingtong_template", "label": "清瞳 · 印章模板"},
    {"key": "qingtong_ocr", "label": "清瞳 · 文字 OCR"},
    {"key": "local:paddle_v6", "label": "本地 · PP-OCRv6"},
    {"key": "local:paddle_seal", "label": "本地 · 印章专用 OCR"},
    {"key": "danzhengtong", "label": "单证通"},
    {"key": "local:legacy_local", "label": "本地 · 历史结果"},
]


def _object_sql(root, fields):
    parts = []
    for key, value in fields.items():
        path = f"{root}.{key}"
        expression = _object_sql(path, value) if isinstance(value, dict) else f"json_extract(r.result_json,'{path}')"
        parts.extend([f"'{key}'", expression])
    return 'json_object(' + ','.join(parts) + ')'


_TEXT_CHECK = dict.fromkeys(('status', 'recognized', 'display_text', 'reliable', 'backend', 'source', 'recognition_mode'))
_SEAL_CHECK = {
    **_TEXT_CHECK, 'local_channel': _TEXT_CHECK,
    'dual_check': {'selected': {
        'template': {'label': None, 'status': None, 'matched': None, 'requirement_match': {'status': None}},
        'ocr': {'text': None, 'status': None, 'matched': None, 'comparison': {'status': None}},
    }},
}
# This is static SQL, assembled only from the constant field allowlists above.
_variant_check = _object_sql('$.details.seal_check', _SEAL_CHECK).replace('r.result_json', 'v.value')
FILTER_RESULT_SQL = (
    "json_object('recognition_config',json_extract(r.result_json,'$.recognition_config'),"
    "'date_check'," + _object_sql('$.date_check', dict.fromkeys(('status', 'actual'))) + ','
    "'seal_check'," + _object_sql('$.seal_check', _SEAL_CHECK) + ','
    "'recognition_variants',json_object('seal',json((SELECT json_group_array(json_object("
    "'method',json_extract(v.value,'$.method'),'error',json_extract(v.value,'$.error'),"
    "'details',json_object('seal_check'," + _variant_check + "))) "
    "FROM json_each(r.result_json,'$.recognition_variants.seal') v))))"
)


def _projection_paths(root, fields):
    return [path for key, value in fields.items()
            for path in (_projection_paths(f'{root}.{key}', value) if isinstance(value, dict)
                         else [f'{root}.{key}'])]


def _extract_sql(source, paths):
    # All paths come from the static allowlists, never request parameters.
    return f"json_extract({source}," + ','.join(f"'{path}'" for path in paths) + ')'


_DATE_CHECK = dict.fromkeys(('status', 'actual'))
_VARIANT_FIELDS = {'method': None, 'error': None, 'details': {'seal_check': _SEAL_CHECK}}
_VARIANT_PATHS = _projection_paths('$', _VARIANT_FIELDS)


def _list_projection_sql():
    # json_each parses the image-heavy original once. Each selected check or
    # variant then uses one multi-path extraction, instead of one per scalar.
    # JSON-encoding the CASE result preserves objects and scalar string values.
    cases = {
        key: "CASE WHEN p.type IN ('object','array') THEN p.value ELSE json_quote(p.value) END"
        for key in ('fields', 'signature_check', 'recognition_config')
    }
    for key, fields in (('date_check', _DATE_CHECK), ('seal_check', _SEAL_CHECK)):
        cases[key] = ("CASE WHEN p.type IN ('object','array') THEN "
                      + _extract_sql('p.value', _projection_paths('$', fields)) + ' END')
    cases['recognition_variants'] = (
        "CASE WHEN p.type IN ('object','array') THEN (SELECT json_group_array("
        + _extract_sql('v.value', _VARIANT_PATHS)
        + ") FROM json_each(p.value,'$.seal') v) ELSE '[]' END"
    )
    return (
        '(SELECT json_group_object(p.key,json(CASE p.key '
        + ' '.join(f"WHEN '{key}' THEN {expression}" for key, expression in cases.items())
        + ' END)) FROM json_each(r.result_json) p WHERE p.key IN ('
        + ','.join(f"'{key}'" for key in cases) + '))'
    )


LIST_RESULT_SQL = _list_projection_sql()


def _restore_projection(values, fields):
    restored = {}
    for key, child in fields.items():
        if isinstance(child, dict):
            restored[key] = _restore_projection(values, child)
        else:
            value = next(values)
            # Single-path SQLite json_extract (the old projection) represents
            # JSON true/false as 1/0, whereas multi-path extraction retains bools.
            restored[key] = int(value) if isinstance(value, bool) else value
    return restored


def decode_list_result(value):
    """Rebuild only allowlisted listing evidence from compact SQL arrays."""
    projected = json.loads(value)
    result = {key: projected.get(key) for key in ('fields', 'signature_check', 'recognition_config')}
    for key, fields in (('date_check', _DATE_CHECK), ('seal_check', _SEAL_CHECK)):
        values = projected.get(key) or [None] * len(_projection_paths('$', fields))
        result[key] = _restore_projection(iter(values), fields)
    result['recognition_variants'] = {'seal': [
        _restore_projection(iter(variant or [None] * len(_VARIANT_PATHS)), _VARIANT_FIELDS)
        for variant in projected.get('recognition_variants') or []
    ]}
    return result


def _present(check):
    return any(check.get(key) is not None for key in ('status', 'actual', 'recognized', 'display_text', 'label', 'text'))


def _accuracy(truth, kind, state, value):
    expected = (("" if truth.get('date_present') is False else truth.get('actual_date')) if kind == 'date'
                else truth.get('seal_should_match'))
    annotated = expected is not None if kind == 'date' else isinstance(expected, bool)
    if not annotated:
        return '待标注'
    if state in {'未测试', '测试中', '未运行', '调用失败', '缺少结果'}:
        return '未评估'
    if kind == 'date':
        normalized = lambda text: parse_date(str(text)) or str(text or '').strip()
        return '正确' if normalized(value) == normalized(expected) else '错误'
    if not value or state not in {'匹配', '不匹配', '部分匹配'}:
        return '未评估'
    return '正确' if (state == '匹配') == expected and state != '部分匹配' else '错误'


def _channel(check, *, value='', job=None, enabled=None, error='', truth=None, kind='seal'):
    check, job = check or {}, job or {}
    status = job.get('status')
    value = str(value or '').strip()
    if not status:
        state, value = '未测试', ''
    elif enabled is False:
        state, value = '未运行', ''
    elif status in {'queued', 'running', 'awaiting_upload'}:
        state, value = '测试中', ''
    elif error or check.get('status') == '识别失败' or (status == 'failed' and not _present(check)):
        state = '调用失败'
    elif not _present(check):
        state, value = '缺少结果', ''
    elif check.get('status') in {'未执行', '未运行'}:
        state, value = '未运行', ''
    elif not value:
        state = '未识别'
    elif (kind == 'seal' and check.get('status') == '匹配'
          and check.get('reliable') is not None and not check['reliable']):
        state = '需人工复核'
    else:
        state = check.get('status') or '需人工复核'
        if state in {'待确认', '匹配待确认'}:
            state = '需人工复核'
    return {'state': state, 'value': value, 'recognized': bool(value),
            'accuracy': _accuracy(truth or {}, kind, state, value)}


def sample_result_filters(job, truth, *, result=None):
    """Use only this job's evidence, including each provider's own verdict."""
    if result is None:
        result = json.loads(job.get('filter_result') or '{}')
    options = json.loads(job.get('payload_json') or '{}')
    options = options.get('sample_options') or options
    config = options.get('recognition_config')
    if not isinstance(config, dict):
        config = result.get('recognition_config')
    config = config if isinstance(config, dict) else None
    primary = result.get('seal_check') or {}
    date_check = result.get('date_check') or {}
    variants = {v.get('method'): v for v in (result.get('recognition_variants') or {}).get('seal') or []}
    methods = set(config.get('seal') or []) if config is not None else set(variants)
    local = primary.get('local_channel') or {}
    if config is None:
        mode = options.get('seal_recognition_mode') or primary.get('recognition_mode')
        selected = (primary.get('dual_check') or {}).get('selected') or {}
        if mode in {'qingtong', 'qingtong_only'} or any(_present(selected.get(key) or {}) for key in ('template', 'ocr')):
            methods.add('qingtong')
        if _present(local):
            methods.add('legacy_local')
        elif mode == 'local' or str(primary.get('backend') or '').startswith('本地'):
            methods.add('legacy_local')
        elif mode == 'danzhengtong' or any('单证通' in str(primary.get(key) or '') for key in ('backend', 'source')):
            methods.add('danzhengtong')
    def enabled(method):
        return method in methods
    def cell(check, value='', **kwargs):
        return _channel(check, value=value, job=job, truth=truth, **kwargs)
    def text(check):
        return check.get('recognized') or check.get('display_text') or ''
    seals = {'combined': cell(primary, text(primary), enabled=bool(methods) if config is not None else None)}
    remote_variant = variants.get('qingtong') or {}
    remote = (remote_variant.get('details') or {}).get('seal_check') or (primary if not variants else {})
    selected = (remote.get('dual_check') or {}).get('selected') or {}
    remote_error = remote_variant.get('error') or ('清瞳识别失败' if remote.get('status') == '识别失败' else '')
    for key, field, comparison in [('template', 'label', 'requirement_match'), ('ocr', 'text', 'comparison')]:
        reading = selected.get(key) or {}
        check = {**reading, **{k: v for k, v in (reading.get(comparison) or {}).items() if v is not None}}
        if not check.get('status') and isinstance(reading.get('matched'), (bool, int)):
            check['status'] = '匹配' if reading['matched'] else '不匹配'
        seals['qingtong_' + key] = cell(check, reading.get(field), enabled=enabled('qingtong'), error=remote_error)
    for method in ('paddle_v6', 'paddle_seal', 'danzhengtong', 'legacy_local'):
        variant = variants.get(method) or {}
        check = (variant.get('details') or {}).get('seal_check') or {}
        if not variants:
            if method == 'legacy_local' or method in methods and method.startswith('paddle'):
                check = local if _present(local) else primary if not enabled('qingtong') else {}
            elif method == 'danzhengtong' and enabled(method):
                check = primary
        check = check.get('local_channel') if _present(check.get('local_channel') or {}) else check
        key = method if method == 'danzhengtong' else 'local:' + method
        seals[key] = cell(check, text(check), enabled=enabled(method), error=variant.get('error') or '')
    return {'date': cell(date_check, date_check.get('actual'), kind='date',
                         enabled=bool(config.get('date')) if config is not None else None), 'seals': seals}
