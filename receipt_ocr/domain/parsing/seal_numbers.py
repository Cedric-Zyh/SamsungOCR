"""Numeric stamp-number OCR compatibility, never arbitrary letter replacement."""
import re


_ONE = r"[（(]\s*[iIｉＩ]\s*[）)]"
_PURPOSE_NUMBER = re.compile(r"((?:专用章|收货章|业务受理章?)\s*)" + _ONE)
_PARENTHESIZED = re.compile(r"[（(][^()（）]*[）)]")


def stamp_number_token(text):
    """Keep one observed parenthesized token in an isolated number field.

    Preserve its contents and bracket shapes. More than one token remains
    ambiguous; the caller must never select one using the required number.
    This is deliberately separate from whole-seal/company normalization.
    """
    matches = _PARENTHESIZED.findall(str(text or ""))
    return matches[0] if len(matches) == 1 else ""


def normalize_stamp_number_letters(text, *, isolated=False):
    """Treat a single parenthesized i as digit one only in a number field.

    ``isolated`` is reserved for an independently located seal-number crop.
    Company names, station IDs, codes and unparenthesized letters stay literal.
    Raw OCR observations remain unchanged at the call site.
    """
    value = str(text or "")
    if isolated and re.fullmatch(r"\s*" + _ONE + r"\s*", value):
        return re.sub(r"[iIｉＩ]", "1", value)
    return _PURPOSE_NUMBER.sub(lambda match: re.sub(r"[iIｉＩ]", "1", match[0]), value)
