"""Recognize printed fields and return their confidence and review reasons."""

from __future__ import annotations
from .document_context import DocumentContext, StageRequest
from .ocr_backends import backend_label
from .field_schema import PRINTED_FIELDS
from .requested_fields import printed_extras
from .parser import (
    LOW_CONFIDENCE_THRESHOLD,
    enrich_fields,
    estimate_field_confidences,
    parse_fields,
    repair_contextual_fields,
    standardize_fixed_phrases,
)
from .field_rules import (
    _is_neighboring_label_misread_as_receipt_note,
    _prefer_detail_field,
    _prefer_detail_requirement,
    _recover_confirmed_template_note,
    _recover_signature_requirement,
    _trim_signature_requirement_candidate,
)


def execute(context: DocumentContext, request: StageRequest):
    source, rows = context.source, context.page(request.route["page"])
    fields = dict(request.fields)
    stage_backends = request.route
    stage_labels = {key: backend_label(value) for key, value in stage_backends.items()}
    qr_text = context.qr()
    field_fallbacks, contextual_corrections, fixed_phrase_corrections = {}, {}, {}
    detail_page_rows = []
    requirement_artifacts = []
    detail_backend = stage_backends["date"]
    invalid_note_original, template_note = "", ""
    if context.document_type["type"] != "receipt":
        fields = enrich_fields(parse_fields(rows), qr_text)
        fixed_phrase_corrections = standardize_fixed_phrases(fields)
    else:
        fields = enrich_fields(parse_fields(rows), qr_text)
        machine_note_original = fields.get("签收说明", "")
        invalid_note_original = ""
        if _is_neighboring_label_misread_as_receipt_note(fields.get("签收说明", "")):
            invalid_note_original = fields.get("签收说明", "")
            fields["签收说明"] = ""
        template_note = _recover_confirmed_template_note(
            rows, fields.get("签收说明", "")
        )
        if template_note:
            fields["签收说明"] = template_note
        # A second stage backend sometimes recognizes handwriting and
        # stamp-adjacent text only in the full-page context. Whenever the date
        # stage runs on another backend, run one supplementary detail-page pass
        # instead of relying exclusively on the small crops.
        detail_page_rows = []
        detail_backend = stage_backends["date"]
        if detail_backend != stage_backends["page"]:
            try:
                detail_page_rows = context.page(detail_backend)
            except Exception:
                detail_page_rows = []
        field_fallbacks = {}
        if detail_page_rows:
            detail_fields = enrich_fields(parse_fields(detail_page_rows), qr_text)
            primary_customer = fields.get("客户名称", "")
            detail_customer = detail_fields.get("客户名称", "")
            if _prefer_detail_field(primary_customer, detail_customer):
                fields["客户名称"] = detail_customer
                field_fallbacks["客户名称"] = {
                    "original": primary_customer,
                    "value": detail_customer,
                    "source": f"{stage_labels['date']} 整页回退",
                }
            # Long stamp requirements are often split into multiple boxes or
            # lose one/two glyphs. Prefer the detail pass only when it is a
            # demonstrably fuller version of the same text, or the primary
            # value is semantically unusable. Unrelated alternatives are never
            # substituted.
            primary_requirement = fields.get("签章要求", "")
            detail_requirement = detail_fields.get("签章要求", "")
            if _prefer_detail_requirement(primary_requirement, detail_requirement):
                fields["签章要求"] = detail_requirement
                field_fallbacks["签章要求"] = {
                    "original": primary_requirement,
                    "value": detail_requirement,
                    "source": f"{stage_labels['seal']} 整页回退",
                }
            # The receipt note is a printed fixed-template field.  If Paddle
            # misses the whole line under a stamp, retain the detail OCR
            # reading first; ``standardize_fixed_phrases`` below will only
            # normalize it when it is sufficiently similar to the known text.
            if (
                not fields.get("签收说明")
                and detail_fields.get("签收说明")
                and not _is_neighboring_label_misread_as_receipt_note(
                    detail_fields.get("签收说明", "")
                )
            ):
                fields["签收说明"] = detail_fields["签收说明"]
                field_fallbacks["签收说明"] = {
                    "original": invalid_note_original,
                    "value": detail_fields["签收说明"],
                    "source": f"{stage_labels['date']} 整页回退",
                }
        requirement_line = _recover_signature_requirement(
            source,
            rows,
            fields.get("签章要求", ""),
            stage_backends["page"],
            customer=fields.get("客户名称", ""),
            artifact_dir=request.artifact_dir,
            artifact_url_prefix=request.artifact_url_prefix,
            artifacts=requirement_artifacts,
        )
        if requirement_line:
            original_requirement = fields.get("签章要求", "")
            fields["签章要求"] = requirement_line["value"]
            field_fallbacks["签章要求"] = {
                "original": original_requirement,
                "value": requirement_line["value"],
                "source": f"{stage_labels['page']} 签章要求行识别",
                "confidence": requirement_line["confidence"],
            }
        normalized_requirement = _trim_signature_requirement_candidate(
            fields.get("签章要求", "")
        )
        if normalized_requirement and normalized_requirement != fields.get(
            "签章要求", ""
        ):
            original_requirement = fields.get("签章要求", "")
            fields["签章要求"] = normalized_requirement
            field_fallbacks["签章要求"] = {
                "original": original_requirement,
                "value": normalized_requirement,
                "source": "固定签章要求业务边界清理",
                "confidence": 0.96,
            }
        contextual_corrections = repair_contextual_fields(fields)
        fixed_phrase_corrections = standardize_fixed_phrases(fields)
        if template_note:
            fixed_phrase_corrections["签收说明"] = {
                "original": machine_note_original,
                "value": template_note,
                "similarity": 1.0,
                "confidence": 0.96,
                "source": "固定签收说明标签 + 模板标准短语校正",
            }
    fields.update(printed_extras(rows))
    field_metadata = estimate_field_confidences(fields, rows, qr_text)
    if field_fallbacks:
        fallback_metadata = estimate_field_confidences(
            fields, detail_page_rows, qr_text
        )
        for name, fallback in field_fallbacks.items():
            metadata = fallback_metadata.get(name, {})
            fallback_confidence = float(
                fallback.get("confidence", metadata.get("confidence", 0.42))
            )
            field_metadata[name] = {
                **metadata,
                "original": fallback.get("original", ""),
                "value": fallback["value"],
                "confidence": fallback_confidence,
                "low_confidence": fallback_confidence < LOW_CONFIDENCE_THRESHOLD,
                "source": fallback["source"],
            }
    for name, correction in fixed_phrase_corrections.items():
        field_metadata[name] = {
            **correction,
            "low_confidence": correction["confidence"] < LOW_CONFIDENCE_THRESHOLD,
        }
    for name, correction in contextual_corrections.items():
        field_metadata[name] = {
            **correction,
            "low_confidence": correction["confidence"] < LOW_CONFIDENCE_THRESHOLD,
        }
    if (
        invalid_note_original
        and "签收说明" not in field_fallbacks
        and not template_note
    ):
        field_metadata["签收说明"] = {
            "original": invalid_note_original,
            "value": "",
            "confidence": 0.2,
            "low_confidence": True,
            "source": "相邻数量字段误入签收说明，已拒绝",
        }
    reasons = []
    if context.document_type["type"] == "receipt":
        critical = {"客户名称", "要求到货", "签章要求"}
        low = sorted(
            name
            for name in critical
            if field_metadata.get(name, {}).get("confidence", 0)
            < LOW_CONFIDENCE_THRESHOLD
        )
        if low:
            reasons.append("关键字段低置信度：" + "、".join(low))
    return {
        **({"processing_artifacts": {"signature_requirement": requirement_artifacts}}
           if requirement_artifacts else {}),
        "fields": fields,
        "field_metadata": {k: v for k, v in field_metadata.items() if k in PRINTED_FIELDS and fields.get(k)},
        "field_fallbacks": field_fallbacks,
        "qr_text": qr_text,
        "stage_review_reasons": reasons,
    }
