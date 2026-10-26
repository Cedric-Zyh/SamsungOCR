"""Strict field boundaries for seals containing contact information.

Inputs have already undergone the seal text typography normalization. Contact
fields may be extra only when their boundaries are unambiguous. Service-station
name prefixes have a separate, explicit centre-and-code rule with a notice.
"""
import re
from .seal_station_fields import compare_station_fields


_LABEL = re.compile(r"授权代码:?|地址:|电话:")
_CONTACT = {"地址", "电话"}
_LEADING_PHONE = re.compile(
    r"(?:电话:)?(?P<phone>0[0-9]{2,3}-[0-9]{7,8})"
    r"(?P<identity>[\u4e00-\u9fff].*)"
)

# A narrow fallback for OCR rows joined without address/telephone labels.
# Require a city, street, house number and a complete landline at the end.
# Never split a numeric code at the length of the expected code.
_UNLABELLED_CONTACT = re.compile(
    r"(?P<code>[0-9]{6,12})(?:地址:)?"
    r"(?P<address>[\u4e00-\u9fff]{2,12}市"
    r"(?:[\u4e00-\u9fff]{1,12}[区县])?"
    r"[\u4e00-\u9fff]{1,20}(?:路|街|巷|大道)"
    r"[0-9]{1,6}(?:-[0-9]{1,6})?号"
    r"(?:(?:首|底|[一二三四五六七八九十]|[0-9]{1,3})[层楼])?)"
    r"(?:电话:)?(?P<phone>0[0-9]{2,3}-[0-9]{7,8})"
)
_IDENTITY_IN_CONTACT = re.compile(
    r"公司|分部|中心|授权|代码|编号|站点|专用章|收货章|业务受理"
)


def _unlabelled_information_fields(text):
    markers = list(re.finditer(r"授权代码:?", text))
    if len(markers) != 1 or markers[0].start() == 0:
        return None
    marker = markers[0]
    contact = _UNLABELLED_CONTACT.fullmatch(text[marker.end():])
    if not contact or _IDENTITY_IN_CONTACT.search(contact["address"]):
        return None
    return {
        "名称": text[:marker.start()],
        "授权代码": contact["code"],
        "地址": contact["address"],
        "电话": contact["phone"],
    }


def _fields(text):
    # QingTong can order the telephone row before the stamp name. Recognize
    # only a complete, hyphen-delimited landline followed by Chinese identity
    # text. Never split at an expected station/code length or drop its digits.
    leading = _LEADING_PHONE.fullmatch(text)
    if leading:
        fields = _fields(leading["identity"])
        if not fields or "电话" in fields:
            return None
        return {**fields, "电话": leading["phone"]}
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
    station = compare_station_fields(expected, actual)
    if station:
        return station
    required, recognized = _fields(expected), _fields(actual)
    if required and "授权代码" in required:
        recognized = _unlabelled_information_fields(actual) or recognized
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
