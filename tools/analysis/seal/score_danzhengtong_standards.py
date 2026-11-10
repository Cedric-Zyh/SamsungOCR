"""Score returned seal fields against frozen, saved standards without I/O.

These are agreement rates with saved standards, not verified OCR accuracy.
The saved reference may itself be OCR-derived or an unverified manual edit.
Strict agreement uses the project's typography/script normalizer. The main
text-match metric additionally accepts an independent trailing seal serial
absent from the saved reference, while preserving every required body glyph.
Business acceptance remains a separate metric with the existing rules.

``score_records`` accepts ``{'standard': {...}, 'response': {...}}`` records.
A response has ``status``, ``commitResult`` and optionally ``req_uuid``.
No field is rewritten to improve a match; raw returned values remain attached.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
import math
import re

from receipt_ocr.domain.parsing.parsing_seals import (
    compare_seal_text_strict,
    normalize_seal_text_strict,
)

FIELD_NAMES = ('收货客户印章', '印章颜色', '印章类型')
METRICS = ('text_match', 'text_exact', 'business_match', 'color_match', 'shape_match', 'all_three')
PENDING_STATUSES = {'new', 'queued', 'uploading', 'uploaded', 'submitting',
                    'submitted', 'waiting', 'pending', 'received'}
SHAPES = {
    **dict.fromkeys(('round', 'circle', 'circular', '圆', '圆章', '圆形', '圆形章', '圆形印章'), 'round'),
    **dict.fromkeys(('ellipse', 'oval', '椭圆', '椭圆章', '椭圆形', '椭圆形章', '椭圆形印章'), 'ellipse'),
    **dict.fromkeys(('rectangle', 'rect', 'rectangular', 'square', '矩形', '矩形章', '矩形印章',
                    '长方形', '长方形章', '长方形印章', '方形', '方章', '正方形', '正方形章'), 'rectangle'),
}
COLORS = {
    **dict.fromkeys(('red', '红', '红色', '红章', '红色印章'), 'red'),
    **dict.fromkeys(('blue', '蓝', '蓝色', '蓝章', '蓝色印章'), 'blue'),
}
NORMALIZATION_NOTE = (
    '文字严格一致仅使用 normalize_seal_text_strict：空白、括号、竖线、ASCII全半角、'
    '已有明确繁简字形与编号字母规范化；不删除编号、电话、地名或其他额外文字。'
)
BASELINE_NOTE = '与保存标准一致率；保存标准可能来自OCR或人工修改，未经逐图核验，不代表全部人工真值。'
TEXT_MATCH_NOTE = (
    '按用户确认口径：标准文字必须完整一致；仅允许原始返回末尾额外的一段13–15位纯数字章号。'
    '章号在原始文字上分离后，正文按既有严格规则规范化比较；公司名、地名、用途、原有编号或电话'
    '有错字/缺字仍不匹配。额外英文、用途、日期、电话及章内序号不在本次放宽范围内。'
    '若标准已包含章号，其数字必须保留且一致；本指标不核验额外章号的逐位正确性。'
)
_ADDITIONAL_SERIAL = re.compile(r'(?<![0-9０-９A-Za-zＡ-Ｚａ-ｚ])([0-9０-９]{13,15})\s*\Z')


def _string(value):
    return '' if value is None else str(value)


def canonical_shape(value):
    """Map documented coarse shape labels; unknown never equals unknown."""
    return SHAPES.get(''.join(_string(value).split()).lower())


def canonical_color(value):
    return COLORS.get(''.join(_string(value).split()).lower())


def _returned_fields(commit):
    fields, errors = {}, []
    if not isinstance(commit, dict):
        return dict.fromkeys(FIELD_NAMES, ''), ['commitResult 必须是对象']
    if commit and not any(name in commit for name in FIELD_NAMES):
        errors.append('commitResult 不包含印章场景字段')
    for name in FIELD_NAMES:
        item = commit.get(name)
        if item is None:
            fields[name] = ''
            continue
        if not isinstance(item, dict):
            fields[name] = ''
            errors.append(f'{name} 必须是字段对象')
            continue
        value = item.get('value')
        if (value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))
                                  or isinstance(value, float) and not math.isfinite(value))):
            fields[name] = ''
            errors.append(f'{name}.value 不是有效标量')
        else:
            fields[name] = _string(value)
    return fields, errors


def _differences(expected, actual):
    changes = []
    for kind, a, b, c, d in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes():
        if kind != 'equal':
            changes.append({'operation': kind, 'expected': expected[a:b], 'returned': actual[c:d],
                            'expected_span': [a, b], 'returned_span': [c, d]})
    return changes


def _difference_description(changes, expected, actual, exact):
    if not expected:
        return '保存标准文字为空，无法判为一致'
    if not actual:
        return '返回印章文字为空'
    if exact:
        return '与保存标准一致（按既有严格规范化）'
    labels = {'replace': '替换', 'delete': '缺少', 'insert': '多出'}
    return '；'.join(f"{labels[item['operation']]}：{item['expected']!r} → {item['returned']!r}"
                    for item in changes)


def _text_match(expected, actual, raw_text, successful):
    """Split the raw trailing digit run before normalizing parentheses/spaces.

    Otherwise an extra ``(1) 11011110146728`` becomes one long number and
    incorrectly hides a meaningful chapter ordinal. Never drop reference
    numbers, accept subsequences, or use similarity to excuse body errors.
    """
    if not successful or not expected or not actual:
        return False, 'unavailable', '', '未返回有效文字或保存标准文字为空'
    if expected == actual:
        return True, 'exact', '', '标准文字严格一致'
    serial = _ADDITIONAL_SERIAL.search(raw_text)
    if serial and normalize_seal_text_strict(raw_text[:serial.start()]) == expected:
        number = normalize_seal_text_strict(serial.group(1))
        return True, 'additional_serial', number, f'正文完整一致，仅额外返回章号 {number}'
    return False, 'mismatch', '', '标准文字或其他附加文字不同；仅允许额外末尾数字章号'


def score_record(standard, response):
    """Score one frozen standard and retain its original reference and return."""
    if not isinstance(standard, dict):
        raise ValueError('standard 必须是标准章对象')
    if not isinstance(response, dict):
        response = {'status': 'invalid_response', 'commitResult': deepcopy(response)}
    status = _string(response.get('status')).strip().lower()
    commit = response.get('commitResult')
    # Accept a durable async child as well as the compact batch response.
    if 'commitResult' not in response and isinstance(response.get('result'), dict):
        commit = response['result'].get('commitResult')
    fields, errors = _returned_fields(commit)
    text = fields['收货客户印章']
    expected_text = _string(standard.get('recognized'))
    requirement = _string(standard.get('requirement'))
    expected = normalize_seal_text_strict(expected_text)
    actual = normalize_seal_text_strict(text)
    has_values = any(value.strip() for value in fields.values())
    if status == 'completed':
        outcome = 'invalid_response' if errors else 'returned' if has_values else 'empty_response'
    elif status in PENDING_STATUSES:
        outcome = 'pending'
    else:
        outcome = 'invalid_response' if status == 'invalid_response' else 'failed'
    success = outcome == 'returned'
    expected_shape, shape = canonical_shape(standard.get('shape')), canonical_shape(fields['印章类型'])
    expected_color, color = canonical_color(standard.get('color')), canonical_color(fields['印章颜色'])
    comparison = compare_seal_text_strict(requirement, [text] if actual else [], mismatch_over_partial=True)
    reference_comparison = compare_seal_text_strict(
        requirement, [expected_text] if expected else [], mismatch_over_partial=True)
    text_match, text_match_kind, ignored_serial, text_match_reason = _text_match(expected, actual, text, success)
    metrics = {
        'text_match': text_match,
        'text_exact': bool(success and expected and actual and expected == actual),
        'business_match': bool(success and actual and comparison.get('status') == '匹配'),
        'color_match': bool(success and expected_color and color and expected_color == color),
        'shape_match': bool(success and expected_shape and shape and expected_shape == shape),
    }
    metrics['all_three'] = all(metrics[name] for name in ('text_match', 'color_match', 'shape_match'))
    source = _string(standard.get('recognized_source')) or 'unknown'
    flags = ['manual_reference_unverified' if source == 'manual' else
             'ocr_reference_unverified' if source == 'ocr' else 'reference_source_unknown']
    if not expected:
        flags.append('empty_reference_text')
    if expected_shape is None:
        flags.append('unknown_reference_shape')
    if expected_color is None:
        flags.append('unknown_reference_color')
    original_ocr = _string(standard.get('ocr_recognized'))
    if original_ocr and normalize_seal_text_strict(original_ocr) != expected:
        flags.append('reference_differs_from_saved_ocr')
    if requirement.strip() and reference_comparison.get('status') != '匹配':
        flags.append('reference_vs_requirement_mismatch')
    changes = _differences(expected, actual)
    return {
        'standard_id': standard.get('id'), 'requirement': requirement,
        'expected_text': expected_text, 'recognized_source': source, 'ocr_recognized': original_ocr,
        'expected_color': standard.get('color'), 'expected_shape': standard.get('shape'),
        'expected_color_canonical': expected_color, 'expected_shape_canonical': expected_shape,
        'returned_text': text, 'returned_color': fields['印章颜色'], 'returned_shape': fields['印章类型'],
        'returned_color_canonical': color, 'returned_shape_canonical': shape,
        'normalized_expected_text': expected, 'normalized_returned_text': actual,
        'status': status, 'outcome': outcome, 'successful_return': success,
        'req_uuid': response.get('req_uuid') or response.get('reqUuid') or '',
        'metrics': metrics, 'business_comparison': comparison,
        'text_match_kind': text_match_kind, 'text_match_reason': text_match_reason,
        'ignored_additional_serial': ignored_serial,
        'reference_business_comparison': reference_comparison,
        'differences': changes,
        'difference_description': _difference_description(changes, expected, actual, metrics['text_exact']),
        'baseline_flags': flags, 'reference_is_verified_ground_truth': False,
        'validation_errors': errors if status == 'completed' or outcome == 'invalid_response' else [],
        'error': response.get('error') or response.get('error_message') or '',
        'raw_commitResult': deepcopy(commit), 'standard': deepcopy(standard),
    }


def _rate(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def _summarize(rows):
    total = len(rows)
    successful = sum(row['successful_return'] for row in rows)
    return {
        'total_count': total, 'completed_status_count': sum(row['status'] == 'completed' for row in rows),
        'successful_return_count': successful,
        'outcome_counts': dict(Counter(row['outcome'] for row in rows)),
        'metrics': {
            metric: {'all_samples': _rate(sum(row['metrics'][metric] for row in rows), total),
                     'successful_returns': _rate(sum(row['metrics'][metric] for row in rows), successful)}
            for metric in METRICS
        },
    }


def summarize_scores(rows):
    """Report valid-return and end-to-end denominators side by side."""
    rows = list(rows)
    summary = _summarize(rows)
    summary.update(
        title='单证通印章与保存标准一致率', baseline_note=BASELINE_NOTE,
        normalization_note=NORMALIZATION_NOTE,
        text_match_note=TEXT_MATCH_NOTE,
        business_match_note='业务签章要求匹配沿用既有业务规则，可接纳符合规则的附加联系方式；不等于文字OCR严格一致。',
        all_three_note='三项全对 = 文字匹配（允许额外章号）+ 颜色一致 + 形状一致；严格文字一致及原业务匹配单列供参考。',
        denominator_note='all_samples 为快照全部标准；successful_returns 仅含 completed 且字段格式有效、至少一项非空的返回。失败、待返回、全空和格式错误均不通过。',
        reference_source_counts=dict(Counter(row['recognized_source'] for row in rows)),
        baseline_flag_counts=dict(Counter(flag for row in rows for flag in row['baseline_flags'])),
        text_match_counts=dict(Counter(row['text_match_kind'] for row in rows)),
        additional_serial_match_count=sum(row['text_match_kind'] == 'additional_serial' for row in rows),
    )
    for key, field in (('by_reference_source', 'recognized_source'),
                       ('by_shape', 'expected_shape_canonical'), ('by_color', 'expected_color_canonical')):
        groups = sorted({row[field] or 'unknown' for row in rows})
        summary[key] = {group: _summarize([row for row in rows if (row[field] or 'unknown') == group])
                        for group in groups}
    return summary


def score_records(records):
    """Evaluate a complete snapshot, including entries still pending or failed."""
    rows = [score_record(record['standard'], record.get('response')) for record in records]
    return {'rows': rows, 'summary': summarize_scores(rows)}
