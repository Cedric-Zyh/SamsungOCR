"""Recognize and compare seal evidence after document routing."""

from __future__ import annotations
from .document_context import DocumentContext, StageRequest
from .image_processing import detect_seal_regions
from .ocr_backends import backend_label
from .parser import compare_seal_text
from .parsing_seals import compare_seal_text_strict
from .document_layout import (
    _exclude_printed_footer_rows_from_seal_context,
    _find_signature_requirement_row,
)
from .recognition_safety import _apply_single_paddle_safety
from .recognition_utils import _dedupe
from .seal_rules import _apply_code_stamp_business_id
from .qingtong_seal import compare_qingtong_seal
from .qingtong_regions import qingtong_region_artifacts, qingtong_seal_regions
from .seal_local_channel import LOCAL_REGION_SOURCE, record_local_channel


def _ellipse_channels(seal_texts, seal_artifacts):
    """Collect oval evidence in ring, centre-type and fallback channels."""
    ring, type_text, other = [], [], []
    for artifact in seal_artifacts:
        if artifact.get("shape") != "椭圆":
            continue
        ring.extend(
            value.strip()
            for value in str(artifact.get("unwrapped_text", "")).split("|")
            if value.strip()
        )
        type_text.extend(
            value.strip()
            for value in str(artifact.get("round_type_band_text", "")).split("|")
            if value.strip()
        )
        for reading in artifact.get("seal_model_readings", []):
            values = [value for value in reading.get("texts", []) if value]
            variant = reading.get("variant", "")
            if variant in {"ellipse_unwrapped", "unwrapped"}:
                ring.extend(values)
            elif variant in {"ellipse_type_band", "round_type_band"}:
                type_text.extend(values)
        other.extend(
            value.strip()
            for value in str(artifact.get("color_isolated_text", "")).split("|")
            if value.strip()
        )
    return _dedupe(ring), _dedupe(type_text), _dedupe(other), _dedupe(seal_texts)


def _ellipse_display_texts(seal_texts, seal_artifacts):
    """Order oval evidence as ring company text, then centre type text."""
    ring, type_text, other, fallback = _ellipse_channels(seal_texts, seal_artifacts)
    return ring + type_text + other or fallback


def _settled_future(future):
    """The prefetched seal response, or ``None`` when it is unusable.

    A local-only run never asks the API anything; it only reuses a request the
    selected QingTong run already made.  A failure there belongs to that run, so
    this must not turn into an exception here.
    """
    if future is None:
        return None
    try:
        return future.result()
    except Exception:
        return None


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
    requirement = fields.get("签章要求", "")
    match_mode = request.acceptance.get("seal_match_mode", "any")
    signature_row = _find_signature_requirement_row(rows)
    has_receipt_footer = signature_row is not None
    signature_anchor = signature_row.y if signature_row else 0.47
    regions, recipient_regions, seal_artifacts = [], [], []
    kind = context.document_type["type"]
    # Ask the seal API before recognising anything ourselves: the stamp boxes it
    # returns are better geometry than a second detection of our own around the
    # footer, so they double as the local region set.  The request stays on the
    # original gates -- remote-only mode, or a footer to attach the stamp to.
    external = None
    remote_check = None
    wants_remote = (
        selected_seal_mode in {"qingtong", "qingtong_only"}
        and context.allows_remote_seal
        and (remote_only or has_receipt_footer)
    )
    if wants_remote:
        external = (
            _seal_api_future.result()
            if _seal_api_future is not None
            else seal_api.recognize(source)
        )
        remote_check = compare_qingtong_seal(
            requirement, external, match_mode=match_mode
        )
        regions = qingtong_seal_regions(source, remote_check)
    elif selected_seal_mode == "local" and _seal_api_future is not None:
        # A local engine was selected next to QingTong, which already asked the
        # API on this run's behalf.  Reuse its box instead of detecting our own,
        # and mark the reading so it can vote as its own channel.
        external = _settled_future(_seal_api_future)
        if isinstance(external, dict) and external.get("ok"):
            remote_check = compare_qingtong_seal(
                requirement, external, match_mode=match_mode
            )
            regions = qingtong_seal_regions(source, remote_check)
    boxed = bool(regions)
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
    elif remote_only and context.allows_remote_seal:
        seal_check = remote_check
        # ``compare_qingtong_seal`` never carries regions: a failed or empty
        # response still needs the key so consumers never see a missing field.
        seal_check.update(
            api=external,
            backend="清瞳印章 API",
            recognition_mode=selected_seal_mode,
            regions=[],
        )
        if not external.get("ok"):
            seal_check.update(
                status="识别失败",
                reliable=False,
                message=external.get("message", "清瞳接口请求失败"),
            )
        else:
            # 本模式按定义不跑本地识别，但清瞳自己的框就是可用证据：落成区域
            # 并裁出证据图，复核页与导出不再是一片空白。
            recipient_regions = list(regions)
            seal_check["regions"] = [
                region.to_dict() for region in recipient_regions
            ]
            seal_artifacts = qingtong_region_artifacts(
                source,
                remote_check,
                artifact_dir,
                artifact_url_prefix,
                ocr_backend_label=stage_labels["seal"],
                note="仅清瞳模式未执行本地识别，此图即清瞳判定所用印章区域",
            )
    else:
        if selected_seal_mode == "qingtong":
            # 未获远程印章授权时该模式原先仍会请求接口，此处保留原路径。
            if remote_check is None:
                external = seal_api.recognize(source)
                remote_check = compare_qingtong_seal(
                    requirement, external, match_mode=match_mode
                )
                regions = regions or qingtong_seal_regions(source, remote_check)
        else:
            external = seal_api.skipped()
        if has_receipt_footer or (boxed and stage_backends["seal"] == "paddle_seal"):
            if regions:
                # 清瞳已经指出印章在哪，本地识别直接用它，不再自行截图。
                recipient_regions = list(regions)
            else:
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
                    and stage_backends["seal"] != "paddle_seal"
                    else None
                ),
                requirement=requirement,
                footer_anchor_y=signature_anchor,
                orientation_mode=request.seal_orientation_mode,
            )
            seal_texts = _dedupe(seal_texts)
            # Local Paddle evidence is shown and judged with the same strict
            # three-way rule as the provider OCR: after ignoring whitespace
            # and Chinese/ASCII parentheses, exact text is a match, an
            # omission-only reading is partial, and any extra/wrong/reordered
            # character is a mismatch.  Keep the fuzzy comparator for routing
            # and audit planning, but do not let it turn a company-only local
            # reading into ``无法判断`` or silently accept a typo.
            has_ellipse_artifact = any(
                artifact.get("shape") == "椭圆" for artifact in seal_artifacts
            )
            if has_ellipse_artifact:
                ring_texts, type_texts, _, _ = _ellipse_channels(
                    seal_texts, seal_artifacts
                )
                # The two ellipse channels describe one stamp. Add their
                # natural reading order as a single candidate so a requirement
                # such as ``公司名收货章`` can reach an exact match while the
                # individual OCR results remain available for audit.
                if ring_texts and type_texts:
                    seal_texts = _dedupe(
                        [ring_texts[0] + type_texts[0], *seal_texts]
                    )
            seal_check = compare_seal_text_strict(requirement, seal_texts)
            if stage_backends["seal"] == "paddle_seal":
                readings = [reading for artifact in seal_artifacts
                            for reading in artifact.get("seal_model_readings", [])]
                errors = [reading["error"] for reading in readings if reading.get("error")]
                seal_check["model_errors"] = errors
                if errors and not seal_texts:
                    seal_check.update(status="识别失败", message="印章专用模型执行失败：" + errors[0])
            # The strict matcher keeps one best candidate for the decision,
            # while an ellipse intentionally has separate centre-row and
            # ring-row channels. Expose all of those channels for both the
            # regular v6 route and the dedicated seal route, so a valid
            # ``收货章`` result is not hidden by the company-name winner.
            if has_ellipse_artifact:
                seal_check["display_text"] = " | ".join(
                    _ellipse_display_texts(seal_texts, seal_artifacts)
                )
            if kind == "receipt":
                seal_check = _apply_code_stamp_business_id(
                    seal_check, requirement, seal_texts, fields
                )
            if boxed:
                # This reading describes the same pixels the API judged, which
                # is what lets it vote as a channel of its own.
                seal_check["region_source"] = LOCAL_REGION_SOURCE
            if selected_seal_mode == 'qingtong':
                local_check = seal_check
                seal_check = (
                    remote_check
                    if remote_check is not None
                    else compare_qingtong_seal(
                        requirement, external, match_mode=match_mode
                    )
                )
                seal_check = record_local_channel(seal_check, local_check, match_mode)
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
                **compare_seal_text(requirement, []),
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
