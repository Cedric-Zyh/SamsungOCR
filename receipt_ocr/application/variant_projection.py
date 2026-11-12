"""Explicit JSON boundary for public provider variants and internal replay.

The public contract keeps its existing payload slots and display values. Only
execution-validity and named provenance fields may accompany them; unknown
metadata stays in internal snapshots rather than being exposed by accident.
"""

from copy import deepcopy

from ..domain.fields.schema import PRINTED_FIELDS
from ..domain.results import StageOutcome


_PUBLIC_EVIDENCE_MARKERS = (
    "pending", "simulated", "mode", "status", "execution_status", "error",
    "source", "backend", "recognition_mode", "provider",
)
_DETAIL_FIELDS = (
    "handwriting_fields", "handwriting_metadata", "fields", "field_metadata",
    "field_fallbacks", "product_table", "date_check", "seal_check",
    "processing_artifacts", "danzhengtong", "danzhengtong_seal",
)
_OPTIONAL_PUBLIC_CONTAINERS = (
    "handwriting_fields", "handwriting_metadata", "fields", "field_metadata",
    "field_fallbacks", "product_table", "date_check", "seal_check",
    "processing_artifacts", "date_ocr_texts",
)


def variant_value(stage, payload):
    """Derive the established display value from observations, never metadata."""
    if stage == "fields":
        return {key: value for key, value in (payload.get("fields") or {}).items() if key in PRINTED_FIELDS}
    if stage == "handwriting":
        return payload.get("handwriting_fields") or {}
    if stage == "products":
        return [row.get("values", {}) for row in (payload.get("product_table") or {}).get("rows", [])]
    check = payload.get("date_check" if stage == "date" else "seal_check") or {}
    return check.get("actual" if stage == "date" else "recognized", ""), check.get("status")


def public_variant(stage, value):
    """Project one outcome without losing the flags that exclude old evidence."""
    outcome = StageOutcome.from_variant(stage, value)
    internal = outcome.to_variant(copy_payload=False)
    payload = internal.get("result")
    details = None
    if payload is not None:
        fields = _DETAIL_FIELDS + (("date_ocr_texts", "preview_date_box") if stage == "date" else ())
        details = {key: payload.get(key) for key in fields}
        # Fields/handwriting/products may publish their own stage validity check
        # even though older projections only listed the date and seal checks.
        for key in (*_PUBLIC_EVIDENCE_MARKERS, f"{stage}_check"):
            if key in payload:
                details[key] = payload[key]
    projected = {key: internal[key] for key in _PUBLIC_EVIDENCE_MARKERS if key in internal}
    projected.update(method=outcome.method, error=internal.get("error"),
                     value=variant_value(stage, payload) if payload is not None else None,
                     details=details)
    return deepcopy(projected)


def replay_variant(stage, value):
    """Restore public evidence and preserve unknown internal envelope metadata.

    Public projection fills absent container slots with null; execution expects
    those optional containers to be absent. Undo only that presentation default,
    retaining provider-trace presence and every actual execution marker.
    """
    if isinstance(value.get("details"), dict):
        details = {key: item for key, item in value["details"].items()
                   if item is not None or key not in _OPTIONAL_PUBLIC_CONTAINERS}
        value = {**value, "details": details}
    return StageOutcome.from_variant(stage, value).to_variant()
