"""Remove one explicitly bounded warehouse-title dot in a comparison copy.

This policy does not change OCR observations or globally normalize punctuation.
The requested warehouse identity supplies the allowed literal comparison only.
"""
from __future__ import annotations

import re


def warehouse_title_punctuation_adjustment(requirement, observed, *, title_only=False):
    """Return an audit for one extra . / · before the code's first digit.

    Only a complete owner-and-code title or complete requested seal identity
    qualifies. The unique warehouse code must begin with an ASCII letter
    prefix immediately followed by a digit. Removing exactly one observed
    dot at that boundary must make every remaining character equal under the
    existing strict seal typography rules. No other punctuation, character,
    name, number, field, or incomplete fragment can be repaired here.

    ``raw_text`` preserves the original observation. ``text``, ``position``
    (zero-based), ``code_index``, and code values use normalized comparison
    forms, so formatting changes remain separate from the audited dot removal.
    """
    if not isinstance(requirement, str) or not isinstance(observed, str):
        return None

    # The strict comparator may call this helper; keep imports inside the
    # comparison boundary to avoid an import cycle.
    from .parsing.parsing_seals import normalize_seal_text_strict
    from .parsing.seal_numbers import normalize_stamp_number_letters
    from .seal_name_terms import seal_name_terms

    expected = normalize_seal_text_strict(requirement)
    actual = normalize_seal_text_strict(observed)
    if not expected or not actual:
        return None

    # Keep terminal number parentheses for field parsing while retaining the
    # existing width, spacing and vertical-bar comparison conventions.
    wording = "".join(
        chr(ord(ch) - 0xFEE0) if "\uff01" <= ch <= "\uff5e" else ch
        for ch in requirement if not ch.isspace() and ch not in "|｜"
    )
    terms = seal_name_terms(normalize_stamp_number_letters(wording))
    if (terms.get('alternatives') or terms.get('parse_status') != 'parsed'
            or not terms.get('name')):
        return None
    codes = [field for field in terms.get('identity_fields') or []
             if field.get('kind') == 'warehouse_code']
    if len(codes) != 1:
        return None
    owner = normalize_seal_text_strict(terms['name'])
    required_code = normalize_seal_text_strict(codes[0].get('value') or '')
    title = owner + required_code
    if not owner or not required_code or not expected.startswith(title):
        return None
    prefix = re.match(r'[A-Za-z]+(?=[0-9])', required_code)
    if prefix is None:
        return None
    if title_only:
        suffix = normalize_seal_text_strict(terms.get('purpose') or '')
        suffix += normalize_seal_text_strict(terms.get('number') or '')
        # A phone or another field must not be silently removed to manufacture
        # an apparently complete first row.
        if expected != title + suffix:
            return None
        expected = title

    position = len(owner) + prefix.end()
    if (len(actual) != len(expected) + 1 or actual[position] not in {'.', '·'}
            or actual[:position] + actual[position + 1:] != expected):
        return None
    removed = actual[position]
    return {
        'raw_text': observed, 'text': expected, 'position': position,
        'code_index': prefix.end(), 'removed': removed,
        'required_code': required_code,
        'raw_code': actual[len(owner):len(title) + 1],
        'corrected_code': required_code,
        'policy': 'warehouse_title_punctuation_from_requirement',
        'changes': [{'index': position, 'code_index': prefix.end(),
                     'before': removed, 'after': ''}],
    }
