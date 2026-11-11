"""Read live and historical evidence without changing its JSON representation.

Payload shape, provider scene and execution validity are separate concerns.
An explicitly saved independent-seal trace is authoritative, even when empty
or failed; it must never acquire the validity of a completed page request.
The views retain their original mappings so callers can preserve raw metadata.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from .evidence_selection import inactive


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def variant_details(variant):
    """Accept public saved variants and internal stage outcomes."""
    return _mapping(variant.get("details") or variant.get("result"))


def provider_trace(document, stage, details=None):
    details, document = _mapping(details), _mapping(document)
    if stage == "seal":
        # Public variants historically projected every evidence key with
        # .get(), so page-only details carry a null seal placeholder. Only that
        # variant placeholder is absent. An explicit document trace (even null)
        # or a non-null variant trace (including {}) remains authoritative.
        if "danzhengtong_seal" in details and details["danzhengtong_seal"] is not None:
            return _mapping(details.get("danzhengtong_seal"))
        if "danzhengtong_seal" in document:
            return _mapping(document.get("danzhengtong_seal"))
        for saved in (details, document):
            check = _mapping(saved.get("seal_check"))
            if check.get("model_id") and isinstance(check.get("candidates"), list):
                return {}  # Dedicated saves missing a trace cannot borrow page evidence.
    for saved in (details, document):
        # Old page-stage payloads used an empty placeholder while keeping the
        # completed trace at document level. Preserve that historical fallback;
        # the independent-seal presence rule above is deliberately stricter.
        if saved.get("danzhengtong"):
            return _mapping(saved.get("danzhengtong"))
    return {}


def completed_real_trace(trace):
    return (isinstance(trace, Mapping) and trace.get("simulated") is False
            and trace.get("mode") == "real" and trace.get("status") == "completed"
            and not inactive(trace))


@dataclass(frozen=True)
class SavedStageEvidence:
    stage: str
    method: str
    details: Mapping
    check: Mapping
    trace: Mapping
    active: bool

    @classmethod
    def from_variant(cls, stage, variant, document=None):
        details = variant_details(variant)
        check = _mapping(details.get(f"{stage}_check"))
        trace = provider_trace(document, stage, details)
        method = variant.get("method", "")
        active = not any(inactive(value) for value in (variant, details, check))
        if method == "danzhengtong":
            active = active and not inactive(trace)
        return cls(stage, method, details, check, trace, active)

    @property
    def real_provider(self):
        return self.active and completed_real_trace(self.trace)
