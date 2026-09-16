"""Ordered date candidate selection and business guards."""

from __future__ import annotations
from .parser import compare_dates, find_receipt_date, parse_date
from .date_evidence import (
    _cross_year_nondestructive_consensus_from_artifacts,
    _cross_year_strict_consensus_from_artifacts,
    _date_component_consensus_from_artifacts,
    _find_confirmed_far_lower_date,
    _find_low_confidence_date_audit,
    _partial_year_day_before_audit_from_artifacts,
    _partial_year_missing_month_business_consensus_from_artifacts,
    _reject_date_before_creation,
    _repeated_server_required_date_from_artifacts,
    _required_month_slot_conflict_consensus_from_artifacts,
    _same_geometry_missing_month_consensus_from_artifacts,
    _same_geometry_partial_year_required_consensus_from_artifacts,
    _server_cross_geometry_date_with_mobile_components_from_artifacts,
    _server_strict_component_consensus_from_artifacts,
    _unanimous_month_day_business_year_consensus_from_artifacts,
)
from .date_decision import DateStageEvidence, DateDecision, DateConsensus


def _select_complete_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    decision.actual_date, decision.date_row = find_receipt_date(
        evidence.combined_rows, evidence.fields.get("要求到货", "")
    )
    if decision.actual_date is None:
        decision.actual_date, decision.date_row = _find_confirmed_far_lower_date(
            evidence.date_rows, evidence.date_artifacts
        )
    consensus.repeated_server_date = _repeated_server_required_date_from_artifacts(
        evidence.date_artifacts, evidence.fields.get("要求到货", "")
    )
    if decision.actual_date is None and consensus.repeated_server_date is not None:
        decision.actual_date = consensus.repeated_server_date
    consensus.server_mobile_component_date = (
        _server_cross_geometry_date_with_mobile_components_from_artifacts(
            evidence.date_artifacts
        )
    )
    if (
        decision.actual_date is None
        and consensus.server_mobile_component_date is not None
    ):
        decision.actual_date = consensus.server_mobile_component_date
    consensus.cross_year_consensus = _cross_year_strict_consensus_from_artifacts(
        evidence.date_artifacts, evidence.fields.get("要求到货", "")
    )
    if consensus.cross_year_consensus is not None:
        decision.actual_date = consensus.cross_year_consensus["date"]
        matching_rows = [
            row
            for row in evidence.date_rows
            if parse_date(row.text) == decision.actual_date
        ]
        if matching_rows:
            decision.date_row = max(matching_rows, key=lambda row: row.confidence)
    consensus.nondestructive_cross_year_consensus = (
        _cross_year_nondestructive_consensus_from_artifacts(
            evidence.date_artifacts, evidence.fields.get("要求到货", "")
        )
    )
    nondestructive_cross_year_markers = {
        str(item.get("date_cross_year_nondestructive_candidate") or "")
        for item in evidence.date_artifacts
        if item.get("date_cross_year_nondestructive_candidate")
    }
    if (
        consensus.nondestructive_cross_year_consensus is not None
        and nondestructive_cross_year_markers
        != {consensus.nondestructive_cross_year_consensus["date"].isoformat()}
    ):
        # The pure helper also audits saved evidence. Only the production
        # path may override final candidate ordering, and that path writes
        # an explicit marker after confirming the current result is still
        # below the reliable threshold.
        consensus.nondestructive_cross_year_consensus = None
    if consensus.nondestructive_cross_year_consensus is not None:
        decision.actual_date = consensus.nondestructive_cross_year_consensus["date"]
        matching_rows = [
            row
            for row in evidence.date_rows
            if parse_date(row.text) == decision.actual_date
        ]
        if matching_rows:
            decision.date_row = max(matching_rows, key=lambda row: row.confidence)


def _select_component_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    consensus.component_consensus = _date_component_consensus_from_artifacts(
        evidence.date_artifacts
    )
    if consensus.component_consensus is not None:
        component_date = consensus.component_consensus["date"]
        if decision.actual_date in {None, component_date}:
            decision.actual_date = component_date
        else:
            # A separately parsed full date is stronger than a value
            # assembled from three crops. Preserve the conflict for review.
            consensus.component_consensus = None
    consensus.server_component_consensus = (
        _server_strict_component_consensus_from_artifacts(evidence.date_artifacts)
    )
    server_component_markers = {
        str(item.get("date_server_component_candidate") or "")
        for item in evidence.date_artifacts
        if item.get("date_server_component_candidate")
    }
    if (
        consensus.server_component_consensus is not None
        and server_component_markers
        != {consensus.server_component_consensus["date"].isoformat()}
    ):
        # Saved evidence may be audited by the pure selector. Only the
        # live macOS Hybrid path may override final ordering after writing
        # the explicit successful component marker.
        consensus.server_component_consensus = None
    if consensus.server_component_consensus is not None:
        server_component_date = consensus.server_component_consensus["date"]
        if decision.actual_date in {None, server_component_date}:
            decision.actual_date = server_component_date
        else:
            consensus.server_component_consensus = None
    consensus.month_slot_conflict_consensus = (
        _required_month_slot_conflict_consensus_from_artifacts(
            evidence.date_artifacts, evidence.fields.get("要求到货", "")
        )
    )
    month_slot_conflict_markers = {
        str(item.get("date_month_slot_conflict_candidate") or "")
        for item in evidence.date_artifacts
        if item.get("date_month_slot_conflict_candidate")
    }
    if (
        consensus.month_slot_conflict_consensus is not None
        and month_slot_conflict_markers
        != {consensus.month_slot_conflict_consensus["date"].isoformat()}
    ):
        # The pure selector is also useful for saved-evidence audits. The
        # live pipeline may override generic candidate ordering only after
        # it ran the four month-slot OCR cells and wrote this marker.
        consensus.month_slot_conflict_consensus = None
    if consensus.month_slot_conflict_consensus is not None:
        decision.actual_date = consensus.month_slot_conflict_consensus["date"]
        matching_rows = [
            row
            for row in evidence.date_rows
            if parse_date(row.text) == decision.actual_date
        ]
        if matching_rows:
            decision.date_row = max(matching_rows, key=lambda row: row.confidence)
    consensus.missing_month_consensus = (
        _same_geometry_missing_month_consensus_from_artifacts(
            evidence.date_artifacts, evidence.fields.get("要求到货", "")
        )
    )
    missing_month_markers = {
        str(item.get("date_missing_month_consensus_candidate") or "")
        for item in evidence.date_artifacts
        if item.get("date_missing_month_consensus_candidate")
    }
    if consensus.missing_month_consensus is not None and missing_month_markers != {
        consensus.missing_month_consensus["date"].isoformat()
    }:
        # Saved artifacts are useful for the full-corpus audit, but only
        # a live Hybrid run that generated the month-slot cells may
        # promote this narrowly reconstructed date.
        consensus.missing_month_consensus = None
    if consensus.missing_month_consensus is not None:
        decision.actual_date = consensus.missing_month_consensus["date"]
        matching_rows = [
            row
            for row in evidence.date_rows
            if parse_date(row.text) == decision.actual_date
        ]
        if matching_rows:
            decision.date_row = max(matching_rows, key=lambda row: row.confidence)


def _select_business_dates(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    consensus.partial_year_required_consensus = (
        _same_geometry_partial_year_required_consensus_from_artifacts(
            evidence.date_artifacts,
            evidence.fields.get("要求到货", ""),
            evidence.fields.get("制单日期", ""),
            evidence.fields.get("运单号", ""),
        )
    )
    if consensus.partial_year_required_consensus is not None:
        partial_year_date = consensus.partial_year_required_consensus["date"]
        if decision.actual_date in {None, partial_year_date}:
            decision.actual_date = partial_year_date
        else:
            consensus.partial_year_required_consensus = None
    consensus.unanimous_month_day_business_year_consensus = (
        _unanimous_month_day_business_year_consensus_from_artifacts(
            evidence.date_artifacts,
            evidence.fields.get("要求到货", ""),
            evidence.fields.get("制单日期", ""),
            evidence.fields.get("运单号", ""),
        )
    )
    if consensus.unanimous_month_day_business_year_consensus is not None:
        consensus_date = consensus.unanimous_month_day_business_year_consensus["date"]
        if decision.actual_date in {None, consensus_date}:
            decision.actual_date = consensus_date
        else:
            consensus.unanimous_month_day_business_year_consensus = None
    consensus.partial_year_missing_month_business_consensus = (
        _partial_year_missing_month_business_consensus_from_artifacts(
            evidence.date_artifacts,
            evidence.fields.get("要求到货", ""),
            evidence.fields.get("制单日期", ""),
            evidence.fields.get("运单号", ""),
        )
    )
    if consensus.partial_year_missing_month_business_consensus is not None:
        component_date = consensus.partial_year_missing_month_business_consensus["date"]
        if decision.actual_date in {None, component_date}:
            decision.actual_date = component_date
        else:
            consensus.partial_year_missing_month_business_consensus = None


def _apply_business_and_review_guards(
    evidence: DateStageEvidence, decision: DateDecision, consensus: DateConsensus
) -> None:
    decision.audit_date_candidate = False
    decision.audit_date_note = ""
    if decision.actual_date is None:
        decision.actual_date, decision.date_row = _find_low_confidence_date_audit(
            evidence.date_rows
        )
        decision.audit_date_candidate = decision.actual_date is not None
        if decision.audit_date_candidate and decision.date_row is not None:
            decision.audit_date_note = (
                "日期行 Server 大模型跨裁剪候选，待人工确认"
                if 0.50 <= decision.date_row.y <= 0.69
                else "远下方手写候选，待人工确认"
            )
    if consensus.cross_year_consensus is not None:
        # Four mandatory Paddle engine/geometry cells with a literal
        # four-digit year outweigh the business-order plausibility
        # heuristic. Keep the anomalous year as a reliable mismatch.
        decision.rejected_date = None
        decision.creation_date = parse_date(evidence.fields.get("制单日期", ""))
    else:
        decision.actual_date, decision.rejected_date, decision.creation_date = (
            _reject_date_before_creation(
                decision.actual_date,
                evidence.fields.get("制单日期", ""),
                evidence.fields.get("运单号", ""),
            )
        )
    consensus.partial_year_day_before_audit = (
        _partial_year_day_before_audit_from_artifacts(
            evidence.date_artifacts,
            evidence.fields.get("要求到货", ""),
            evidence.fields.get("制单日期", ""),
        )
    )
    decision.partial_year_day_before_selected = False
    decision.superseded_rejected_date = None
    if consensus.partial_year_day_before_audit is not None and (
        decision.actual_date is None or decision.rejected_date is not None
    ):
        decision.superseded_rejected_date = decision.rejected_date
        decision.actual_date = consensus.partial_year_day_before_audit["date"]
        decision.date_row = None
        decision.rejected_date = None
        decision.audit_date_candidate = False
        decision.audit_date_note = ""
        decision.partial_year_day_before_selected = True
        for artifact in evidence.date_artifacts:
            if artifact.get("variant") in {"紧凑区域", "宽区域"}:
                artifact.update(
                    {
                        "date_partial_year_day_before_candidate": (
                            decision.actual_date.isoformat()
                        ),
                        "date_partial_year_day_before_note": (
                            "Mobile/Server 在紧裁、宽裁四个最大通道单元"
                            "均读到同一显式月日；年份仅保留 20/20x，"
                            "故固定为低置信度人工建议，不参与自动放行"
                        ),
                    }
                )
    if not evidence.has_receipt_footer:
        decision.actual_date, decision.date_row, decision.rejected_date = (
            None,
            None,
            None,
        )
    if decision.rejected_date:
        # A receipt cannot be signed before the outbound document exists.
        # Keep all OCR text and intermediate images for review, but do not
        # display an impossible glyph reconstruction as a real mismatch.
        decision.date_row = None
    decision.date_check = compare_dates(
        evidence.fields.get("要求到货", ""), decision.actual_date
    )
    if evidence.rejected_date_evidence:
        decision.date_check["rejected_candidates"] = evidence.rejected_date_evidence
    if consensus.cross_year_consensus is not None and evidence.rejected_date_evidence:
        accepted_business_anomalies = [
            item
            for item in evidence.rejected_date_evidence
            if str(item.get("value") or "") == decision.actual_date.isoformat()
        ]
        remaining_rejections = [
            item
            for item in evidence.rejected_date_evidence
            if str(item.get("value") or "") != decision.actual_date.isoformat()
        ]
        if accepted_business_anomalies:
            decision.date_check["business_time_overridden_candidates"] = (
                accepted_business_anomalies
            )
        if remaining_rejections:
            decision.date_check["rejected_candidates"] = remaining_rejections
        else:
            decision.date_check.pop("rejected_candidates", None)
    if not evidence.has_receipt_footer:
        decision.date_check.update(
            status="未识别",
            message="回单首页未包含签收页脚，等待关联商品续页",
        )
    if decision.rejected_date:
        decision.date_check.update(
            rejected_candidate=decision.rejected_date.isoformat(),
            rejection_reason=(
                f"OCR 候选早于制单日期 {decision.creation_date.isoformat()}，已转人工复核"
            ),
            message="日期 OCR 候选违反业务时间顺序，未用于自动判定",
        )
    if decision.partial_year_day_before_selected:
        decision.date_check["partial_year_day_before_audit"] = {
            "candidate": decision.actual_date.isoformat(),
            "models": ["mobile", "server"],
            "geometries": ["紧凑区域", "宽区域"],
            "policy": "残缺年份四单元一致，仅作人工建议",
            "support": consensus.partial_year_day_before_audit["support"],
        }
        if decision.superseded_rejected_date is not None:
            decision.date_check["superseded_rejected_candidate"] = (
                decision.superseded_rejected_date.isoformat()
            )
        decision.date_check["message"] += (
            "（Mobile/Server 紧裁与宽裁四单元均支持该月日，"
            "但年份残缺；仅供人工复核）"
        )
