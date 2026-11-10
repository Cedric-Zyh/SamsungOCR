"""Select one existing date observation without altering provider evidence."""
from copy import deepcopy

from .evidence_selection import inactive
from .parsing.parsing_dates import parse_date
from .printed_field_selection import canonical_printed_field


def _details(variant):
    details = variant.get('result')
    return details if isinstance(details, dict) else variant.get('details') or {}


def _check(variant):
    return _details(variant).get('date_check') or {}


def _parsed(value):
    return parse_date(canonical_printed_field('要求到货', value)) if isinstance(value, str) else None


def _usable(variant):
    details, check = _details(variant), _check(variant)
    return bool(check) and not any(inactive(value) for value in
                                  (variant, details, check, details.get('danzhengtong')))


def select_date_variant(variants, *, required_date=None):
    """Prefer a reliable match, then the nearest complete observed date.

    Equal distances retain provider order. Incomplete dates are never completed
    from the requirement, and selecting a candidate does not change its verdict.
    Accept both pipeline results and the saved public variant representation.
    """
    candidates = [variant for variant in variants if _usable(variant)]
    if not candidates:
        return variants[0] if variants else None
    matching = [variant for variant in candidates if _check(variant).get('status') == '匹配'
                and _check(variant).get('reliable') is True
                and _parsed(_check(variant).get('actual')) is not None]
    if matching:
        return matching[0]
    required = _parsed(required_date)
    if required_date is None:
        required = next((value for variant in candidates
                         if (value := _parsed(_check(variant).get('required'))) is not None), None)
    dated = [(variant, actual) for variant in candidates
             if (actual := _parsed(_check(variant).get('actual'))) is not None]
    if required is not None and dated:
        return min(dated, key=lambda value: abs((value[1] - required).days))[0]
    return candidates[0]


def _human_date(result):
    check = result.get('date_check') or {}
    metadata = (result.get('field_metadata') or {}).get('签收日期') or {}
    return (result.get('review_status') in {'确认通过', '确认不通过'}
            or bool(result.get('page_review_override'))
            or any('人工' in str(value.get(key, '')) for value in (check, metadata)
                   for key in ('source', 'backend'))
            or check.get('human_confirmed')
            or 'human_confirmed_match' in check)


def _pending(variant):
    details, check = _details(variant), _check(variant)
    states = {'pending', 'waiting', 'running', 'queued', 'submitting', 'submitted',
              'polling', '识别中', '待识别', '测试中'}
    return any(value.get('pending') or str(value.get('status', '')).lower() in states
               for value in (variant, details, check, variant.get('danzhengtong') or {},
                             details.get('danzhengtong') or {}))


def refresh_date_selection(current):
    """Reproject a saved machine date only; keep all original evidence and verdicts.

    Persisting callers must separately protect human review history and active
    jobs under their write lock. This function performs no I/O or provider calls.
    """
    result = deepcopy(current)
    variants = (current.get('recognition_variants') or {}).get('date') or []
    if (_human_date(current) or not variants or _pending(current)
            or any(_pending(variant) for variant in variants)):
        return result
    successful = [variant for variant in variants if _usable(variant)]
    if not successful:
        return result
    required = (current.get('fields') or {}).get('要求到货') or (current.get('date_check') or {}).get('required')
    selected = select_date_variant(successful, required_date=required)
    check = deepcopy(_check(selected))
    acceptance = (current.get('recognition_config') or {}).get('acceptance') or {}
    mode = acceptance.get('date_match_mode')
    if mode == 'none':
        check.update(status='未核对', reliable=True, message='按设置跳过签收日期核对')
    elif mode == 'all':
        if (len(successful) != len(variants) or not all(
                _check(variant).get('status') == '匹配' and _check(variant).get('reliable') is True
                for variant in successful)):
            check.update(status='需人工复核', reliable=False,
                         message='全部识别结果匹配模式下，签收日期结果未全部匹配')
    elif mode != 'any':
        values = {(_check(variant).get('actual'), _check(variant).get('status')) for variant in successful}
        if len(values) > 1:
            check.update(status='需人工复核', reliable=False)
        elif any(_check(variant).get('reliable') is not True for variant in successful):
            check['reliable'] = False
    result['date_check'] = check
    result.setdefault('fields', {})['签收日期'] = str(check.get('actual_display') or check.get('actual') or '')
    return result
