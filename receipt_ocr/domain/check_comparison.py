"""Machine comparison rules; evidence trust and human confirmation live outside.

These functions compare observations with a requirement. They never infer a
manual confirmation, upgrade OCR confidence, or choose a provider response.
"""

from .parsing import compare_dates, parse_date
from .parsing.parsing_seals import compare_seal_text_strict
from .printed_field_selection import canonical_printed_field


STRICT_SEAL_POLICIES = frozenset({"strict_text", "strict_information_fields"})


def compare_machine_date(requirement, text):
    return compare_dates(canonical_printed_field("要求到货", requirement),
                         parse_date(canonical_printed_field("要求到货", text)))


def compare_machine_seals(requirement, texts, *, local=False, reliable=None):
    # Conflicting characters from the local reader must not be hidden by a
    # shorter omission-only fragment. Independent provider stamps retain their
    # own comparison policy and are never concatenated into one stamp.
    comparison = compare_seal_text_strict(requirement, texts, mismatch_over_partial=local)
    if reliable is not None:
        comparison["reliable"] = bool(comparison.get("reliable") and reliable is True)
    return comparison
