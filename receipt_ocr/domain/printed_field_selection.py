"""Choose each printed field from usable provider evidence independently."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
import re

from .evidence_selection import choose_candidate, confidence, inactive
from .fields.schema import PRINTED_FIELDS


_QUANTITIES = {"实收数量", "拒收数量", "合计数量"}
# Limit width normalization to ASCII. NFKC would also equate ① and 1, which
# can be distinct parts of a company's required seal name.
_WIDTHS = str.maketrans({**{chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}, "\u3000": " "})
_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")
_NUMBER = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, int) or isinstance(value, float) and isfinite(value):
        return str(value)
    return ""


def _date_value(text):
    match = _DATE.fullmatch(text)
    if match:
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            pass
    return None


def _number_value(text):
    if _NUMBER.fullmatch(text):
        try:
            value = Decimal(text.replace(",", ""))
            rendered = format(value, "f")
            return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
        except InvalidOperation:
            pass
    return None


def canonical_printed_field(name, value):
    """Normalize equivalent formats without turning unlike text into a match."""
    text = "".join(_text(value).translate(_WIDTHS).split())
    if name == "要求到货":
        return _date_value(text) or text
    if name in _QUANTITIES:
        number = _number_value(text)
        return text if number is None else number
    return text


def _valid_format(name, value):
    if name == "要求到货":
        return _date_value(value) is not None
    if name in _QUANTITIES:
        return _number_value(value) is not None
    return True


def _candidates(variants, name):
    candidates = []
    for variant in variants:
        if not isinstance(variant, dict) or inactive(variant):
            continue
        details = variant.get("result")
        if not isinstance(details, dict):
            details = variant.get("details") or {}
        if not isinstance(details, dict) or inactive(details):
            continue
        statuses = details.get("recognition_status") or {}
        stage_status = statuses.get("fields") if isinstance(statuses, dict) else None
        if (inactive({"status": stage_status}) or inactive(details.get("fields_check"))
                or inactive(details.get("danzhengtong"))):
            continue
        fields = details.get("fields")
        if not isinstance(fields, dict):
            fields = variant.get("value") or {}
        if not isinstance(fields, dict):
            continue
        value = _text(fields.get(name))
        if not value:
            continue
        all_metadata = details.get("field_metadata") or {}
        metadata = all_metadata.get(name) or {} if isinstance(all_metadata, dict) else {}
        if not isinstance(metadata, dict) or inactive(metadata):
            continue
        normalized = canonical_printed_field(name, value)
        if metadata.get("value") is not None and canonical_printed_field(name, metadata["value"]) != normalized:
            metadata = {}
        fallbacks = details.get("field_fallbacks") or {}
        fallback = fallbacks.get(name) if isinstance(fallbacks, dict) else None
        if not isinstance(fallback, dict) or canonical_printed_field(name, fallback.get("value")) != normalized:
            fallback = None
        candidates.append({
            "method": variant.get("method"), "value": value, "normalized": normalized,
            "metadata": metadata, "confidence": confidence(metadata), "fallback": fallback,
            "valid_format": _valid_format(name, normalized),
        })
    return candidates


def select_printed_fields(variants):
    """Select seven independently sourced fields from live or saved variants.

    Original values and evidence stay intact. An absent selected method is not
    permission for a saved-result repair to erase a previously stored value.
    """
    fields, metadata, fallbacks, selections, conflicts = {}, {}, {}, {}, []
    for name in PRINTED_FIELDS:
        candidates = _candidates(variants, name)
        valid = [candidate for candidate in candidates if candidate["valid_format"]]
        selected, disputed, reason = choose_candidate(valid or candidates, label=name)
        fields[name] = selected["value"] if selected else ""
        if selected:
            metadata[name] = deepcopy(selected["metadata"])
            metadata[name].setdefault("value", selected["value"])
            metadata[name].setdefault("original", selected["value"])
            metadata[name].setdefault("source", selected["method"])
            if not selected["valid_format"]:
                metadata[name]["low_confidence"] = True
                reason = "识别结果格式需核对"
            if selected["fallback"] is not None:
                fallbacks[name] = deepcopy(selected["fallback"])
        selections[name] = {
            "selected_method": selected["method"] if selected else None,
            "conflict": disputed, "reason": reason,
        }
        if disputed:
            conflicts.append(name)
    return {"fields": fields, "metadata": metadata, "field_fallbacks": fallbacks,
            "selections": selections, "conflicts": conflicts}
