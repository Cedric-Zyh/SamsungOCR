"""Conservative suggestions from saved truth and the current machine evidence.

This module never saves annotations or infers image defects. Suggestions remain
observations for a reviewer, separate from the machine's acceptance decision.
"""
from datetime import date
import re

from .fields.schema import PENDING_PROVIDER_FIELDS, PRINTED_FIELDS
from .problem_seal_channels import seal_problem_channels


_NOT_RUN = {'skipped', 'pending', 'queued', 'running', 'cancelled', 'not_run',
            '未执行', '未运行', '未测试', '已跳过', '待识别', '未核对'}
_FAILED = {'failed', 'error', '识别失败', '执行失败', '失败', '错误'}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _date(value):
    match = re.fullmatch(r'(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?', _text(value))
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    return None


def _disabled(result, stage, method=''):
    plan = _mapping(result.get('recognition_config'))
    if stage not in plan:
        return False
    methods = plan[stage]
    return not methods or (method and isinstance(methods, list) and method not in methods)


def _executed(result, stage, check, variant=None):
    variant = _mapping(variant)
    method = _text(variant.get('method'))
    if _disabled(result, stage, method):
        return False
    for evidence in (result, variant, check):
        if (_text(evidence.get('execution_status')) in _NOT_RUN
                or _text(evidence.get('status')) in _NOT_RUN):
            return False
    if _text(check.get('status')) in _NOT_RUN or check.get('source') == '按设置跳过':
        return False
    # A selected provider alone does not prove that recognition happened.
    return bool(check) or bool(_text(variant.get('error')))


def _failure(evidence):
    explicit = _text(evidence.get('error_message')) or _text(evidence.get('error'))
    if explicit:
        return explicit
    if any(_text(evidence.get(key)) in _FAILED
           for key in ('execution_status', 'status', 'overall', 'final_result')):
        return _text(evidence.get('message')) or '识别执行失败'
    return ''


def _provider_label(method):
    return {'qingtong': '清瞳', 'paddle_v6': 'PaddleOCR',
            'paddle_seal': 'PaddleOCR 印章', 'danzhengtong': '单证通'}.get(method, method)


def _field_value(name, value):
    if name == '要求到货' and (normalized := _date(value)):
        return normalized
    return ''.join(_text(value).split())


def _field_available(name, value, metadata):
    if any(_text(metadata.get(key)) in _NOT_RUN | _FAILED for key in ('execution_status', 'status')):
        return False
    if any(mark in _text(metadata.get('source')) for mark in ('待提供', '待单证通', '未执行', '已跳过')):
        return False
    # These cells may be intentionally blank until their provider supplies them.
    if name in PENDING_PROVIDER_FIELDS and not _text(value):
        return False
    return True


def _seal_channels(result):
    yield from seal_problem_channels(result, _executed, _failure)


def _seal_problem_type(source, kind):
    prefix = {'qingtong': '清瞳印章', 'local': '本地印章'}.get(source)
    if not prefix:
        return kind
    suffix = {'印章判定错误': '判定错误', '印章未检出': '未检出',
              '识别执行失败': '识别失败', '证据不足待核对': '证据不足'}
    return prefix + suffix[kind]


def suggest_problems(entry, result):
    """Return unique, explained labels; leave both input objects unchanged."""
    entry, result = _mapping(entry), _mapping(result)
    reasons, executed_scopes = {}, set()

    def add(kind, reason):
        group = reasons.setdefault(kind, [])
        if reason not in group:
            group.append(reason)

    failure = _failure(result)
    if failure:
        add('识别执行失败', failure)
    for stage, variants in _mapping(result.get('recognition_variants')).items():
        if stage == 'seal':
            continue  # Stamp failures are attributed to their independent source below.
        for variant in variants if isinstance(variants, list) else []:
            variant = _mapping(variant)
            variant_failure = _failure(variant)
            if variant_failure and _executed(result, stage, variant, variant):
                label = _provider_label(_text(variant.get('method'))) or '识别通道'
                add('识别执行失败', f'{label}：{variant_failure}')

    saved_date = _date(entry.get('actual_date')) if entry.get('date_present') is not False else None
    required = _date(_mapping(entry.get('fields')).get('要求到货'))
    if saved_date and required and saved_date != required:
        add('原单日期不符', f'人工标注签收日期为 {saved_date}，要求到货日期为 {required}')
    date_check = _mapping(result.get('date_check'))
    if _executed(result, 'date', date_check):
        executed_scopes.add('date')
        date_failure = _failure(date_check)
        if date_failure:
            add('识别执行失败', f'日期：{date_failure}')
        actual = _text(date_check.get('actual'))
        actual_date = _date(actual)
        blank = (entry.get('date_present') is False
                 or (entry.get('date_present') is not True
                     and 'actual_date' in entry and isinstance(entry['actual_date'], str)
                     and not entry['actual_date'].strip()))
        if blank and actual:
            add('空白误识别日期', f'人工标注日期为空白，本次却识别为 {actual}')
        elif saved_date and actual_date and saved_date != actual_date:
            add('日期读错', f'人工标注为 {saved_date}，本次识别为 {actual_date}')
        elif saved_date and not actual and not (date_failure or failure):
            add('日期漏识别', f'人工已标注日期 {saved_date}，本次日期识别没有读到日期')
        if date_check.get('reliable') is False and not date_failure:
            add('证据不足待核对', '本次日期识别尚未形成可靠证据')

    expected_seal = entry.get('seal_should_match')
    seal_failures = False
    for source, label, check, variant in _seal_channels(result):
        executed_scopes.add('seal')
        channel_failure = _failure(variant) or _failure(check)
        if channel_failure:
            add(_seal_problem_type(source, '识别执行失败'), f'{label}：{channel_failure}')
            seal_failures = True
            continue
        status = _text(check.get('status'))
        if isinstance(expected_seal, bool):
            expected = '匹配' if expected_seal else '不匹配'
            if status in {'匹配', '不匹配'} and status != expected:
                add(_seal_problem_type(source, '印章判定错误'), f'{label}判定{status}，人工标注应{expected}')
            value = _text(check.get('recognized')) or _text(check.get('display_text'))
            if (expected_seal is True and not value
                    and (status in {'未识别', '未检出', '未检测到'} or check.get('detected') is False)):
                add(_seal_problem_type(source, '印章未检出'), f'{label}已执行，但未获得可读印章内容')
        if check.get('reliable') is False or status in {'部分匹配', '无法判断', '需人工复核'}:
            add(_seal_problem_type(source, '证据不足待核对'), f'{label}尚未形成可靠的印章判定')

    fields = _mapping(entry.get('fields'))
    actual_fields = _mapping(result.get('fields'))
    if _executed(result, 'fields', actual_fields):
        executed_scopes.add('fields')
        metadata = _mapping(result.get('field_metadata'))
        different = [name for name in PRINTED_FIELDS
                     if _text(fields.get(name)) and name in actual_fields
                     and _field_available(name, actual_fields[name], _mapping(metadata.get(name)))
                     and not (failure and not _text(actual_fields[name]))
                     and _field_value(name, fields[name]) != _field_value(name, actual_fields[name])]
        if different:
            add('基础字段错误', f'本次识别与已保存人工标注不同：{"、".join(different)}')
        if any(_field_available(name, actual_fields.get(name), _mapping(metadata.get(name)))
               and _mapping(metadata.get(name)).get('low_confidence') is True for name in PRINTED_FIELDS):
            add('证据不足待核对', '本次基础字段存在低置信度识别结果')

    issues = result.get('review_issues')
    for issue in issues if isinstance(issues, list) else []:
        issue = _mapping(issue)
        scope = _text(issue.get('scope'))
        if (_text(issue.get('code')) == 'provider_conflict'
                and (scope in executed_scopes or (scope == 'document' and executed_scopes))):
            add('证据不足待核对', _text(issue.get('message')) or '本次已执行的识别通道存在分歧')
        if (_text(issue.get('code')) == 'provider_failure' and not _disabled(result, scope)
                and not (scope == 'seal' and seal_failures)):
            add('识别执行失败', _text(issue.get('message')) or '识别服务执行失败')
    return [{'type': kind, 'reason': '；'.join(messages)} for kind, messages in reasons.items()]
