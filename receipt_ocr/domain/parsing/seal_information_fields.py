"""Strict field boundaries for seals containing contact information.

Inputs have already undergone the seal text typography normalization. Only
explicitly labelled contact fields may be extra; identity fields never may.
"""
import re


_LABEL = re.compile(r"授权代码:?|地址:|电话:")
_CONTACT = {"地址", "电话"}


def _fields(text):
    markers = list(_LABEL.finditer(text))
    if not markers:
        return {"名称": text} if text else None
    name = text[:markers[0].start()]
    if not name:
        return None
    fields = {"名称": name}
    for index, marker in enumerate(markers):
        key = marker.group().rstrip(":")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        value = text[marker.end():end]
        if not value or key in fields:
            return None
        # Do not trim trailing characters: they may be a conflicting identity.
        if key == "授权代码" and not re.fullmatch(r"[0-9]+", value):
            return None
        if key == "电话" and not re.fullmatch(r"[0-9]+(?:-[0-9]+)*", value):
            return None
        fields[key] = value
    return fields


def compare_information_fields(expected, actual):
    """Return evidence only for an unambiguous, complete field-level match."""
    required, recognized = _fields(expected), _fields(actual)
    if not required or not recognized or len(recognized) == 1:
        return None
    if any(recognized.get(key) != value for key, value in required.items()):
        return None
    extra = set(recognized) - set(required)
    if not extra <= _CONTACT:
        return None
    matched = list(required)
    ignored = [key for key in recognized if key in extra]
    message = "、".join(matched) + "一致"
    if ignored:
        message += "；" + "、".join(ignored) + "为签章要求未列出的附加信息"
    return {
        "matched_fields": matched,
        "additional_fields": ignored,
        "message": message,
    }
