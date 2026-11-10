"""Exact stamp identity with explicitly bounded phone/serial evidence."""
import re


_WIDTHS = str.maketrans({chr(n): chr(n - 0xFEE0) for n in range(0xFF01, 0xFF5F)})
_PHONE_SEPARATOR = r"(?:\s*-\s*|\s+)"
_LANDLINE = rf"0[0-9]{{2,3}}{_PHONE_SEPARATOR}[0-9]{{7,8}}"
_MOBILE = r"1[3-9][0-9]{9}"
_LABELLED_NUMBER = rf"(?:0[0-9]{{2,3}}{_PHONE_SEPARATOR}?[0-9]{{7,8}}|{_MOBILE}|[1-9][0-9]{{6,7}})"
_PHONE_LABEL = r"(?:联系电话|电话|手机|TEL)\s*:?\s*"
_SERIAL_LABEL = r"(?:印章编号|章号|编号)\s*:?\s*"
_TAILS = (
    ('电话', re.compile(rf"{_PHONE_LABEL}(?P<value>{_LABELLED_NUMBER})\s*$", re.I)),
    ('章号', re.compile(rf"(?:{_SERIAL_LABEL})?\(\s*(?P<value>[0-9]{{13,15}})\s*\)\s*$")),
    ('章号', re.compile(rf"{_SERIAL_LABEL}(?P<value>[0-9]{{13,15}})\s*$")),
    ('电话', re.compile(rf"(?<![0-9A-Za-z])(?P<value>{_LANDLINE})\s*$")),
    ('电话', re.compile(rf"(?<!\S)(?P<value>{_MOBILE})\s*$")),
    ('章号', re.compile(r"(?<![0-9A-Za-z])(?P<value>[0-9]{13,15})\s*$")),
)
_LEADING_PHONE = re.compile(
    rf"^(?:{_PHONE_LABEL})?(?P<value>{_LANDLINE}|{_MOBILE})\s*(?=[\u4e00-\u9fff])", re.I)


def _parts(raw, normalize):
    # Separate raw fields before the strict normalizer removes parentheses.
    # In particular, '(1) 11011110146728' must never become one serial number.
    body = str(raw or '').translate(_WIDTHS).strip()
    fields, evidence = {}, {}
    for _ in range(4):
        found = None
        for key, pattern in _TAILS:
            match = pattern.search(body)
            if match:
                found = key, match
                break
        if found is None:
            break
        key, match = found
        if key in fields:
            return None
        fields[key] = re.sub(r"[-\s]", '', match['value']) if key == '电话' else match['value']
        evidence[key] = match.group().strip()
        body = body[:match.start()].rstrip()
    leading = _LEADING_PHONE.match(body)
    if leading:
        if '电话' in fields:
            return None
        fields['电话'] = re.sub(r"[-\s]", '', leading['value'])
        evidence['电话'] = leading.group().strip()
        body = body[leading.end():]
    identity = normalize(body)
    if not identity or not re.search(r"[\u4e00-\u9fff]", identity):
        return None
    return {'名称': identity, **fields}, evidence


def compare_auxiliary_fields(requirement, recognized, normalize):
    """Ignore only extra bounded fields, never missing/different required text.

    An unlabelled phone needs a hyphen/space-delimited area code, or a
    standalone mobile-number token. Unlabelled serials use this workflow's
    13–15-digit scope, not a claim about a legal serial-number format.
    All required digits remain mandatory; extras are not verified for accuracy.
    """
    expected, actual = _parts(requirement, normalize), _parts(recognized, normalize)
    if expected is None or actual is None:
        return None
    required, required_evidence = expected
    observed, observed_evidence = actual
    if len(required) == len(observed) == 1:
        return None
    if any(observed.get(key) != value for key, value in required.items()):
        return None
    extras = [key for key in observed if key not in required]
    if not set(extras) <= {'电话', '章号'}:
        return None
    message = '、'.join(required) + '一致'
    if extras:
        message += '；' + '、'.join(extras) + '为签章要求未列出的附加信息'
    return {'matched_fields': list(required), 'additional_fields': extras,
            'required_fields': required, 'recognized_fields': observed,
            'required_field_evidence': required_evidence,
            'recognized_field_evidence': observed_evidence,
            'message': message}
