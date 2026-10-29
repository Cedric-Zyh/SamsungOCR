"""Select usable handwriting evidence without confusing absence with disagreement."""

from copy import deepcopy

from .fields.schema import HANDWRITTEN_FIELDS
from .evidence_selection import inactive as _inactive, confidence as _confidence, choose_candidate


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def canonical_handwriting_fields(result):
    """Compare only owned fields, ignoring whitespace and absent/empty keys."""
    fields = result.get("handwriting_fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    return {name: "".join(_text(fields.get(name)).split()) for name in HANDWRITTEN_FIELDS}


def _candidates(variants, name):
    candidates = []
    for variant in variants:
        if not isinstance(variant, dict) or _inactive(variant):
            continue
        details = variant.get("result")
        if not isinstance(details, dict):
            details = variant.get("details") or {}
        if not isinstance(details, dict) or _inactive(details):
            continue
        if _inactive(details.get("danzhengtong")) or _inactive(details.get("handwriting_check")):
            continue
        fields = details.get("handwriting_fields")
        if not isinstance(fields, dict):
            fields = variant.get("value") or {}
        if not isinstance(fields, dict):
            continue
        value = _text(fields.get(name))
        if not value:
            continue
        all_metadata = details.get("handwriting_metadata") or {}
        metadata = all_metadata.get(name) or {} if isinstance(all_metadata, dict) else {}
        if not isinstance(metadata, dict) or _inactive(metadata):
            continue
        # Metadata for a different value cannot justify the current candidate.
        if metadata.get("value") is not None and "".join(_text(metadata["value"]).split()) != "".join(value.split()):
            metadata = {}
        candidates.append({
            "method": variant.get("method"), "value": value,
            "normalized": "".join(value.split()), "metadata": metadata,
            "confidence": _confidence(metadata),
        })
    return candidates


def select_handwriting(variants):
    """Accept live or saved variants; return copied fields and their real provenance.

    A selected method of ``None`` means that no real, nonempty candidate is
    available. Refreshing an old record must not treat that as evidence to erase
    an existing value. Selection never uses the printed warehouse contact.
    """
    fields, metadata, methods, reasons = {}, {}, [], []
    conflict = False
    for name in HANDWRITTEN_FIELDS:
        selected, disputed, reason = choose_candidate(_candidates(variants, name), label="签收人")
        fields[name] = selected["value"] if selected else ""
        conflict = conflict or disputed
        reasons.append(reason)
        if selected:
            methods.append(selected["method"])
            metadata[name] = deepcopy(selected["metadata"])
            metadata[name].setdefault("value", selected["value"])
            metadata[name].setdefault("original", selected["value"])
            metadata[name].setdefault("source", selected["method"])
    return {
        "fields": fields, "metadata": metadata,
        "selected_method": methods[0] if methods else None,
        "conflict": conflict, "reason": "；".join(dict.fromkeys(reasons)),
    }
