"""Recognize and compare seal evidence after document routing."""

from __future__ import annotations
from .document_context import DocumentContext, StageRequest
from .image_processing import detect_seal_regions
from .ocr_backends import backend_label
from .parser import compare_seal_text
from .document_layout import (
    _exclude_printed_footer_rows_from_seal_context,
    _find_signature_requirement_row,
)
from .recognition_safety import _apply_single_paddle_safety
from .recognition_utils import _dedupe
from .seal_rules import _apply_code_stamp_business_id
from .qingtong_seal import compare_qingtong_seal


def execute(context: DocumentContext, request: StageRequest, recognize_seals, seal_api):
    source = context.source
    remote_only = request.seal_mode == "qingtong_only"
    rows = [] if remote_only else context.page(request.route["page"])
    fields, stage_backends = dict(request.fields), request.route
    stage_labels = {key: backend_label(value) for key, value in stage_backends.items()}
    artifact_dir, artifact_url_prefix = (
        request.artifact_dir,
        request.artifact_url_prefix,
    )
    selected_seal_mode, _seal_api_future = request.seal_mode, request.seal_future
    detail_page_rows = (
        context.cached_page(stage_backends["date"])
        if stage_backends["date"] != stage_backends["page"]
        else []
    )
    signature_row = _find_signature_requirement_row(rows)
    has_receipt_footer = signature_row is not None
    signature_anchor = signature_row.y if signature_row else 0.47
    regions, recipient_regions, seal_artifacts = [], [], []
    kind = context.document_type["type"]
    if kind not in {"receipt", "product_continuation", "unclassified"} or (
        kind == "product_continuation" and not context.has_footer
    ):
        seal_check = {
            "requirement": fields.get("签章要求", ""),
            "recognized": "",
            "all_recognized": [],
            "score": 0.0,
            "status": "无法判断",
            "message": "文档类型分流后未执行固定位置印章识别",
            "confidence": 0.0,
            "reliable": False,
            "backend": "未执行（文档类型分流）",
            "regions": [],
            "api": {"enabled": False, "message": "文档类型分流后未调用印章 API"},
            "recognition_mode": selected_seal_mode,
        }
    elif selected_seal_mode == "qingtong_only" and context.allows_remote_seal:
        external = (
            _seal_api_future.result()
            if _seal_api_future is not None
            else seal_api.recognize(source)
        )
        seal_check = compare_qingtong_seal(
            fields.get("签章要求", ""), external,
            match_mode=request.acceptance.get("seal_match_mode", "any"),
        )
        seal_check.update(
            api=external, backend="清瞳印章 API", recognition_mode=selected_seal_mode
        )
        if not external.get("ok"):
            seal_check.update(
                status="识别失败",
                reliable=False,
                message=external.get("message", "清瞳接口请求失败"),
            )
    else:
        if has_receipt_footer:
            regions = detect_seal_regions(source)
            recipient_regions = [
                region for region in regions if region.role == "收货客户章"
            ]
            seal_texts, seal_artifacts = recognize_seals(
                source,
                _exclude_printed_footer_rows_from_seal_context(
                    detail_page_rows or rows
                ),
                recipient_regions,
                artifact_dir,
                artifact_url_prefix,
                stage_backends["seal"],
                secondary_ocr_backend=(
                    stage_backends["page"]
                    if stage_backends["page"] != stage_backends["seal"]
                    else None
                ),
                requirement=fields.get("签章要求", ""),
                footer_anchor_y=signature_anchor,
            )
            external = (
                seal_api.recognize(source)
                if selected_seal_mode == "qingtong"
                else seal_api.skipped()
            )
            seal_texts = _dedupe(seal_texts)
            seal_check = compare_seal_text(fields.get("签章要求", ""), seal_texts)
            if kind == "receipt":
                seal_check = _apply_code_stamp_business_id(
                    seal_check, fields.get("签章要求", ""), seal_texts, fields
                )
            if selected_seal_mode == 'qingtong':
                local_check = seal_check
                seal_check = compare_qingtong_seal(
                    fields.get('签章要求', ''), external,
                    match_mode=request.acceptance.get("seal_match_mode", "any"),
                )
                seal_check['local_evidence'] = local_check
            local_label = stage_labels["seal"]
            seal_check["backend"] = (
                f"清瞳印章 API + 本地 {local_label}"
                if external.get("ok")
                else f"本地 {local_label}"
            )
            seal_check["recognition_mode"] = selected_seal_mode
            seal_check["regions"] = [region.to_dict() for region in recipient_regions]
            seal_check["api"] = external
        else:
            regions, recipient_regions, seal_artifacts = [], [], []
            external = {
                "enabled": False,
                "requested": selected_seal_mode == "qingtong",
                "message": "首页无签收页脚，未调用印章 API",
            }
            seal_check = {
                **compare_seal_text(fields.get("签章要求", ""), []),
                "status": "无法判断",
                "message": "回单首页未包含签收页脚，等待关联商品续页",
                "backend": "未执行（等待关联续页）",
                "regions": [],
                "api": external,
                "recognition_mode": selected_seal_mode,
            }
    safety = _apply_single_paddle_safety(
        {}, {} if remote_only or seal_check.get('dual_check') else seal_check, stage_backends
    )
    reasons = [] if seal_check.get("reliable") else ["印章内容无法可靠判断"]
    return {
        "seal_check": seal_check,
        "seal_regions": [region.to_dict() for region in regions],
        "processing_artifacts": {"seals": seal_artifacts},
        "safety_policy": safety,
        "stage_review_reasons": reasons,
    }


def complete_evidence(result: dict, reference_matcher=None) -> dict:
    """Complete optional reference evidence after the selected variants merge."""
    if reference_matcher is not None:
        from .seal_reference_policy import apply_reference_evidence

        apply_reference_evidence(result, reference_matcher)
    return result
