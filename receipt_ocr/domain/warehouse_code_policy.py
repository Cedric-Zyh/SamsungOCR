"""Requirement-directed 0/O comparison for one complete warehouse identity.

This policy changes a comparison copy only. It is not OCR evidence or a
general character equivalence; the caller must retain the original readings.
"""
from __future__ import annotations


def warehouse_code_adjustment(requirement, observed, *, title_only=False):
    """Return an auditable warehouse-code comparison adjustment, or ``None``.

    Every differing character must be an uppercase O / digit zero replacement
    inside the requirement's sole warehouse-code field. All other characters
    remain equal under the existing strict seal typography rules. ``text``
    and the zero-based change positions use those normalized comparison forms;
    ``raw_text`` preserves the complete caller-provided observation.

    ``title_only`` accepts the complete name-and-code first row of a seal
    requirement, never a code fragment, purpose row, or unverified substring.
    """
    if not isinstance(requirement, str) or not isinstance(observed, str):
        return None

    # Import lazily: the strict comparator itself calls this policy.
    from .parsing.parsing_seals import normalize_seal_text_strict
    from .parsing.seal_numbers import normalize_stamp_number_letters
    from .seal_name_terms import seal_name_terms

    expected = normalize_seal_text_strict(requirement)
    actual = normalize_seal_text_strict(observed)
    if not expected or not actual:
        return None

    # Preserve number parentheses for field parsing while applying the same
    # ASCII-width/spacing conventions as the strict comparison boundary.
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
    required_code = normalize_seal_text_strict(codes[0].get('value') or '')
    owner = normalize_seal_text_strict(terms['name'])
    title = owner + required_code
    if not owner or not required_code or not expected.startswith(title):
        return None
    if title_only:
        suffix = normalize_seal_text_strict(terms.get('purpose') or '')
        suffix += normalize_seal_text_strict(terms.get('number') or '')
        # Additional fields or ambiguous wording cannot become a first row
        # merely by dropping characters until the observation fits.
        if expected != title + suffix:
            return None
        expected = title
    if len(actual) != len(expected) or actual == expected:
        return None

    start, end = len(owner), len(title)
    changes = []
    for index, (before, after) in enumerate(zip(actual, expected)):
        if before == after:
            continue
        if not start <= index < end or {before, after} != {'0', 'O'}:
            return None
        changes.append({'index': index, 'code_index': index - start,
                        'before': before, 'after': after})
    if not changes:
        return None
    return {
        'raw_text': observed, 'text': expected,
        'required_code': required_code, 'raw_code': actual[start:end],
        'corrected_code': required_code,
        'policy': 'warehouse_code_zero_o_from_requirement', 'changes': changes,
    }
