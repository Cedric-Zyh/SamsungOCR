"""Select a date from OCR-owned rows without cross-model repair rules."""

from __future__ import annotations

from receipt_ocr.domain.parsing import compare_dates, find_receipt_date
from receipt_ocr.recognition.date.postprocess.state import (
    DateStageEvidence,
    DateDecision,
    DateConsensus,
)


def _select_complete_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    """Choose the best complete date that OCR actually read."""
    # Pass an empty requirement deliberately. ``find_receipt_date`` accepts a
    # required date as a parsing prior and can repair missing components from
    # it; the structured date flow must select only a literal OCR-owned date.
    decision.actual_date, decision.date_row = find_receipt_date(
        evidence.combined_rows,
        "",
        anchor_y=evidence.anchor_y,
        trusted_region=evidence.trusted_region,
    )


def _select_component_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    # Components are exposed for display, but missing digits are never filled
    # from the required date or another model.
    return None


def _select_business_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    # Business fields may explain a result downstream; they never reconstruct
    # an OCR value in the date stage.
    return None


def _apply_business_and_review_guards(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    decision.audit_date_candidate = False
    decision.audit_date_note = ""
    decision.rejected_date = None
    decision.creation_date = None
    if not evidence.has_receipt_footer:
        decision.actual_date = None
        decision.date_row = None
        decision.date_check = {
            "required": evidence.fields.get("要求到货", ""),
            "actual": "",
            "status": "未识别",
            "message": "回单首页未包含签收页脚，等待关联商品续页",
            "confidence": 0.0,
            "reliable": False,
        }
        return
    decision.date_check = compare_dates(
        evidence.fields.get("要求到货", ""), decision.actual_date
    )
