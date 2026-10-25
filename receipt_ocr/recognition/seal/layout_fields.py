"""Classify observed layout text without using the requested stamp wording."""
import re
from receipt_ocr.domain.parsing.seal_numbers import normalize_stamp_number_letters, stamp_number_token


def phone_value(text):
    value = re.sub(r'\s+', '', text or '')
    # Normalize the contact label only, never substitute digits.
    match = re.fullmatch(
        r'(?:(?:电话|tel|te1)[:：.]?)?(\d{3,4})[-—－](\d{7,8})',
        value, flags=re.IGNORECASE,
    )
    return f'{match[1]}-{match[2]}' if match else ''


def inner_role(text, *, number_field=False):
    value = re.sub(r'\s+', '', text or '')
    if phone_value(value):
        return 'phone'
    number = stamp_number_token(value) if number_field else value
    if re.fullmatch(r'[（(]\d{1,3}[）)]', normalize_stamp_number_letters(number, isolated=True)):
        return 'number'
    if re.fullmatch(r'\d{4,12}站', value):
        return 'station'
    if value.endswith('章') or value == '业务受理':
        return 'type'
    if re.fullmatch(r'[\u4e00-\u9fff]{2,10}部', value):
        return 'department'
    return ''
