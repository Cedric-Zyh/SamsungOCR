"""Explicit state shared by the ordered date decision rules."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from .ocr_types import TextObservation


@dataclass
class DateStageEvidence:
    fields: dict
    combined_rows: list[TextObservation]
    date_rows: list[TextObservation]
    date_artifacts: list[dict]
    has_receipt_footer: bool
    # y of the signature-requirement row. The receiving-date band is an offset
    # from it, so a footer that sits lower than the usual 0.47 must carry the
    # band with it instead of losing every read.
    anchor_y: float | None = None
    rejected_date_evidence: list[dict] = field(default_factory=list)


@dataclass
class DateDecision:
    actual_date: date | None = None
    date_row: TextObservation | None = None
    date_check: dict = field(default_factory=dict)
    date_confidence: float = 0.0
    audit_date_candidate: bool = False
    audit_date_note: str = ""
    rejected_date: date | None = None
    creation_date: date | None = None
    partial_year_day_before_selected: bool = False
    superseded_rejected_date: date | None = None


@dataclass
class DateConsensus:
    repeated_server_date: date | None = None
    server_mobile_component_date: date | None = None
    cross_year_consensus: dict | None = None
    nondestructive_cross_year_consensus: dict | None = None
    component_consensus: dict | None = None
    server_component_consensus: dict | None = None
    month_slot_conflict_consensus: dict | None = None
    missing_month_consensus: dict | None = None
    partial_year_required_consensus: dict | None = None
    unanimous_month_day_business_year_consensus: dict | None = None
    partial_year_missing_month_business_consensus: dict | None = None
    partial_year_day_before_audit: dict | None = None
