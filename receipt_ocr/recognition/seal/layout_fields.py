"""Classify observed layout text without using the requested stamp wording."""
import re


def phone_value(text):
    value = re.sub(r'\s+', '', text or '')
    # Normalize the contact label only, never substitute digits.
    match = re.fullmatch(
        r'(?:(?:电话|tel|te1)[:：.]?)?(\d{3,4})[-—－](\d{7,8})',
        value, flags=re.IGNORECASE,
    )
    return f'{match[1]}-{match[2]}' if match else ''


def inner_role(text):
    value = re.sub(r'\s+', '', text or '')
    if phone_value(value):
        return 'phone'
    if re.fullmatch(r'[（(]\d{1,3}[）)]', value):
        return 'number'
    if re.fullmatch(r'\d{4,12}站', value):
        return 'station'
    if value.endswith('章') or value == '业务受理':
        return 'type'
    return ''
