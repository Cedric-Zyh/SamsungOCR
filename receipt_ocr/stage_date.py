"""Route documents and apply date verification rules in a fixed order."""

from __future__ import annotations
import re
from .document_context import DocumentContext, StageRequest
from .parser import (
    compare_dates,
    compare_partial_date_components,
    estimate_date_confidence,
    extract_date_components,
    find_receipt_date,
    parse_date,
)
from .date_evidence import _collect_business_rejected_date_evidence
from .document_layout import _find_signature_requirement_row
from .ocr_types import TextObservation
from .recognition_safety import _apply_single_paddle_safety
from .date_decision import DateStageEvidence, DateDecision, DateConsensus
from .date_selection import (
    _select_complete_dates,
    _select_component_dates,
    _select_business_dates,
    _apply_business_and_review_guards,
)
from .date_confidence_rules import (
    _score_primary_evidence,
    _score_confirmed_audit_markers,
    _score_complete_consensus,
    _score_business_consensus,
    _apply_review_confidence_caps,
)


def _tight_decision_rows(rows, artifacts):
    """Return observations published by the tight date crop only."""
    tight = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"), None
    )
    if not tight or "decision_rows" not in tight:
        selected = list(rows)
    else:
        encoded = tight.get("decision_rows") or []
        selected = []
        for item in encoded:
            try:
                selected.append(TextObservation(**item))
            except (TypeError, ValueError):
                continue
        if not selected:
            # The low-confidence audit deliberately publishes one capped
            # strict-date observation when the date-line variants agree but
            # do not satisfy the automatic-decision gate.  Keep that review
            # candidate visible to the date comparison instead of reducing
            # the result to a blank component.  Its capped confidence keeps
            # it on the human-review path.
            selected = [
                row
                for row in rows
                if float(row.confidence) <= 0.25 and parse_date(row.text) is not None
            ]
    # A fragment such as ``202年2月5日`` exposes a malformed/incomplete year.
    # It must not inherit the required year and turn the uncertain day into a
    # complete date. Keep the original OCR in the artifact, but exclude this
    # row from the live compact-date decision; missing components remain blank.
    return [
        row
        for row in selected
        if not re.search(r"(?<!\d)\d{1,3}年", str(row.text or ""))
    ]


def _tight_decision_artifacts(artifacts):
    tight = [item for item in artifacts if item.get("variant") == "紧凑区域"]
    return tight or list(artifacts)


def _partial_date_check(required_text, rows):
    components = extract_date_components(rows)
    check = compare_partial_date_components(required_text, components)
    observed_count = sum(value is not None for value in components.values())
    if observed_count:
        best_confidence = max((float(row.confidence) for row in rows), default=0.0)
        # A partial date is useful evidence but can never reach the reliable
        # automatic-match threshold until all three components are present.
        check["confidence"] = round(
            min(0.68, best_confidence * observed_count / 3.0), 3
        )
    return check


def execute(context: DocumentContext, request: StageRequest, recognize_date):
    source, rows = context.source, context.page(request.route["page"])
    fields = dict(request.fields)
    stage_backends = request.route
    artifact_dir, artifact_url_prefix = (
        request.artifact_dir,
        request.artifact_url_prefix,
    )
    detail_page_rows = (
        context.cached_page(stage_backends["date"])
        if stage_backends["date"] != stage_backends["page"]
        else []
    )
    signature_row = _find_signature_requirement_row(rows)
    has_receipt_footer = signature_row is not None
    signature_anchor = signature_row.y if signature_row else 0.47
    date_row, date_rows, date_artifacts = None, [], []
    all_date_rows, all_date_artifacts = [], []
    kind = context.document_type["type"]
    if kind == "product_continuation" and has_receipt_footer:
        all_date_rows, all_date_artifacts = recognize_date(
            source,
            signature_anchor,
            "",
            artifact_dir,
            artifact_url_prefix,
            stage_backends["date"],
            secondary_ocr_backend=(
                stage_backends["page"]
                if stage_backends["page"] != stage_backends["date"]
                else None
            ),
            allow_strict_date_without_requirement=True,
        )
        date_rows = _tight_decision_rows(all_date_rows, all_date_artifacts)
        date_artifacts = _tight_decision_artifacts(all_date_artifacts)
        actual_date, date_row = find_receipt_date(date_rows, "")
        date_confidence = estimate_date_confidence(date_rows, "", actual_date)
        partial_check = _partial_date_check("", date_rows)
        date_check = {
            **(compare_dates("", actual_date) if actual_date else partial_check),
            "message": (
                "续页已识别实际收货日期，关联首页后再核验"
                if actual_date
                else "续页" + partial_check.get(
                    "message", "签收页脚未可靠识别到实际日期"
                )
            ),
            "confidence": date_confidence,
            "reliable": bool(actual_date and date_confidence >= 0.72),
            "source": "紧凑区域日期识别",
        }
    elif kind != "receipt":
        date_check = {
            "required": fields.get("要求到货", ""),
            "actual": "",
            "status": "无法判断",
            "message": "文档类型分流后未执行固定位置日期识别",
            "confidence": 0.0,
            "reliable": False,
        }
    else:
        if has_receipt_footer:
            all_date_rows, all_date_artifacts = recognize_date(
                source,
                signature_anchor,
                fields.get("要求到货", ""),
                artifact_dir,
                artifact_url_prefix,
                stage_backends["date"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["date"]
                    else None
                ),
                creation_text=fields.get("制单日期", ""),
            )
            date_rows = _tight_decision_rows(all_date_rows, all_date_artifacts)
            date_artifacts = _tight_decision_artifacts(all_date_artifacts)
        else:
            all_date_rows, all_date_artifacts = [], []
            date_rows, date_artifacts = [], []
        # The compact crop is the only date evidence generated and used for
        # the live comparison.
        combined_rows = list(date_rows)
        evidence = DateStageEvidence(
            fields=fields,
            combined_rows=combined_rows,
            date_rows=date_rows,
            date_artifacts=date_artifacts,
            has_receipt_footer=has_receipt_footer,
            anchor_y=signature_anchor,
        )
        evidence.rejected_date_evidence = _collect_business_rejected_date_evidence(
            date_rows,
            fields.get("要求到货", ""),
            fields.get("制单日期", ""),
            fields.get("运单号", ""),
        )
        decision, consensus = DateDecision(), DateConsensus()
        # Candidate ordering and confidence caps are intentionally separate.
        # Every rule runs in the same order as the original receipt stage.
        _select_complete_dates(evidence, decision, consensus)
        _select_component_dates(evidence, decision, consensus)
        _select_business_dates(evidence, decision, consensus)
        _apply_business_and_review_guards(evidence, decision, consensus)
        _score_primary_evidence(evidence, decision, consensus)
        _score_confirmed_audit_markers(evidence, decision, consensus)
        _score_complete_consensus(evidence, decision, consensus)
        _score_business_consensus(evidence, decision, consensus)
        _apply_review_confidence_caps(evidence, decision, consensus)
        date_check, date_row = decision.date_check, decision.date_row
        if has_receipt_footer and decision.actual_date is None:
            partial_check = _partial_date_check(fields.get("要求到货", ""), date_rows)
            if partial_check.get("actual_display"):
                date_check = partial_check
        if decision.actual_date is not None:
            date_check.setdefault("actual_display", decision.actual_date.isoformat())
        if has_receipt_footer:
            date_check.setdefault("source", "紧凑区域日期识别")
    safety = _apply_single_paddle_safety(date_check, {}, stage_backends)
    reasons = [] if date_check.get("reliable") else ["收货日期无法可靠判断"]
    return {
        "date_check": date_check,
        "date_ocr_texts": [row.text for row in all_date_rows],
        "processing_artifacts": {"date": all_date_artifacts},
        "safety_policy": safety,
        "preview_date_box": (
            (date_row.x, date_row.y, date_row.width, date_row.height)
            if date_row
            else None
        ),
        "stage_review_reasons": reasons,
    }
