"""Route documents and apply date verification rules in a fixed order."""

from __future__ import annotations
from .document_context import DocumentContext, StageRequest
from .parser import compare_dates, estimate_date_confidence, find_receipt_date
from .date_evidence import _collect_business_rejected_date_evidence
from .document_layout import _find_signature_requirement_row
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
    kind = context.document_type["type"]
    if kind == "product_continuation" and has_receipt_footer:
        date_rows, date_artifacts = recognize_date(
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
        actual_date, date_row = find_receipt_date(rows + date_rows, "")
        date_confidence = estimate_date_confidence(date_rows, "", actual_date)
        date_check = {
            **compare_dates("", actual_date),
            "message": (
                "续页已识别实际收货日期，关联首页后再核验"
                if actual_date
                else "续页签收页脚未可靠识别到实际日期"
            ),
            "confidence": date_confidence,
            "reliable": bool(actual_date and date_confidence >= 0.72),
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
            date_rows, date_artifacts = recognize_date(
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
        else:
            date_rows, date_artifacts = [], []
        combined_rows = rows + detail_page_rows + date_rows
        evidence = DateStageEvidence(
            fields=fields,
            combined_rows=combined_rows,
            date_rows=date_rows,
            date_artifacts=date_artifacts,
            has_receipt_footer=has_receipt_footer,
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
    safety = _apply_single_paddle_safety(date_check, {}, stage_backends)
    reasons = [] if date_check.get("reliable") else ["收货日期无法可靠判断"]
    return {
        "date_check": date_check,
        "date_ocr_texts": [row.text for row in date_rows],
        "processing_artifacts": {"date": date_artifacts},
        "safety_policy": safety,
        "preview_date_box": (
            (date_row.x, date_row.y, date_row.width, date_row.height)
            if date_row
            else None
        ),
        "stage_review_reasons": reasons,
    }
