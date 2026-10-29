"""Resolve one observed receiving-purpose row without consulting a target.

The common 货/费 OCR disagreement is resolved only when this same physical
row actually supplied 收货专用章. A number may come from another reading of
that row, but distinct observed number glyphs remain a conflict.
"""
from copy import deepcopy
import math
import re


POLICY = 'observed_receiving_purpose'
_PURPOSE = re.compile(r'^(收[货费]专用章)([①-⑳]|\([0-9０-９]{1,3}\)|（[0-9０-９]{1,3}）)?$')
_FAILED = {'failed', 'failure', 'error', '识别失败', '执行失败', '失败', '错误',
           '未运行', '未执行', '已跳过'}


def real_observation(value):
    return (isinstance(value, dict) and not value.get('simulated') and not value.get('error')
            and (value.get('status') is None or isinstance(value.get('status'), str))
            and value.get('status') not in _FAILED)


def _reliable(value):
    if not real_observation(value) or isinstance(value.get('confidence'), bool):
        return False
    try:
        confidence = float(value.get('confidence'))
    except (TypeError, ValueError):
        return False
    return math.isfinite(confidence) and .75 <= confidence <= 1


def _literal(text):
    return re.sub(r'\s+', '', str(text or ''))


def resolve_observed_purpose(values, row_index):
    """Return an auditable same-row resolution, or retain the old conflict."""
    if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0 or not values:
        return None
    if not all(_reliable(value) for value in values):
        return None
    parsed = [_PURPOSE.fullmatch(_literal(value.get('text'))) for value in values]
    if not all(parsed) or {match[1] for match in parsed} != {'收货专用章', '收费专用章'}:
        return None
    numbers = {match[2] for match in parsed if match[2]}
    if len(numbers) > 1:
        return None
    number = next(iter(numbers), '')
    return {'policy': POLICY, 'accepted': True, 'row_index': row_index,
            'purpose_text': '收货专用章', 'number_text': number,
            'text': '收货专用章' + number, 'observations': deepcopy(values)}


def resolved_purpose_covers(value, decision, identity_text, *, row_index=None):
    """Exempt only an actual same-row observation covered by this resolution.

    Callers must bind variants to their physical row and use that input's own
    decision. Unmapped old OCR observations cannot borrow this exception.
    """
    resolution = (decision or {}).get('purpose_resolution') or {}
    selected_text = resolution.get('text')
    if row_index is None:
        row_index = value.get('row_index', value.get('rectangle_row_index'))
    if (not _reliable(value) or isinstance(row_index, bool) or not isinstance(row_index, int)
            or resolution.get('policy') != POLICY or resolution.get('accepted') is not True
            or row_index != resolution.get('row_index')
            or not isinstance(selected_text, str) or not selected_text
            or selected_text not in _literal(identity_text)):
        return False
    observed = _literal(value.get('text'))
    return bool(observed and any(_literal(item.get('text')) == observed
                                and _reliable(item) for item in resolution.get('observations') or []))
