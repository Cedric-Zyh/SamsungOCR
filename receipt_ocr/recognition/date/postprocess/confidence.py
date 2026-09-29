"""Confidence for the one-model, clean-date-line decision."""

from __future__ import annotations

from receipt_ocr.domain.parsing import estimate_date_confidence
from receipt_ocr.recognition.date.postprocess.state import (
    DateStageEvidence,
    DateDecision,
    DateConsensus,
)


def _score_primary_evidence(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    decision.date_confidence = estimate_date_confidence(
        evidence.date_rows,
        evidence.fields.get("要求到货", ""),
        decision.actual_date,
    )


def _finish(decision: DateDecision) -> None:
    decision.date_check["confidence"] = decision.date_confidence
    decision.date_check["reliable"] = bool(
        decision.actual_date and decision.date_confidence >= 0.72
    )


def _score_confirmed_audit_markers(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    return None


def _score_complete_consensus(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    return None


def _score_business_consensus(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    return None


def _apply_review_confidence_caps(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    _finish(decision)
