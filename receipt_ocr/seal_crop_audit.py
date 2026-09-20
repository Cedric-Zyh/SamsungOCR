"""Read Server seal evidence, reconstruct supported text and record its provenance."""

from __future__ import annotations

from pathlib import Path

from .ocr_backends import backend_label, recognize_text
from .recognition_utils import _dedupe
from .seal_audit_policy import variant_for_audit_backend
from .seal_rules import (
    _reconstruct_business_acceptance_from_audit,
    _reconstruct_exact_company_stamp_from_region,
    _reconstruct_one_error_round_type_band,
    _reconstruct_overlapping_repair_stamp,
    _reconstruct_partitioned_service_organization,
    _shared_long_organization_suffix,
    combine_region_texts,
)
from .seal_crop_types import (
    SealCropRequest,
    SealEvidenceCollection,
    SealAuditRoute,
    SealAuditEvidence,
)


def _read_server_audit_evidence(
    audit: SealAuditEvidence, route: SealAuditRoute
) -> None:
    """Keep audit-only model readings separate from accepted matching text.

    The provider comes from the resolved policy rather than a literal, so the
    call can never be denied by the request scope without the caller knowing.
    """
    backend = route.audit_backend
    if not backend:
        return
    variant = variant_for_audit_backend(backend)
    audit.audit_variant_texts: dict[str, list[str]] = {}
    for audit_label, audit_path in audit.audit_paths:
        if not audit_path or not Path(audit_path).is_file():
            continue
        try:
            if audit_label in {"矩形编号章数字行", "圆章章类型横向分带"}:
                from .paddle_ocr import recognize_line

                audit_rows = recognize_line(
                    audit_path, model_variant=variant
                )
            else:
                audit_rows = recognize_text(
                    audit_path,
                    backend=backend,
                    min_text_height=0.012,
                )
            current_audit_texts = [
                row.text for row in audit_rows if row.text
            ]
            if audit_label == "圆章章类型横向分带":
                audit.round_type_band_rows = list(audit_rows)
            # The robust-bound bands are a cross-model route.
            # Keep Server-only readings visible for audit but
            # do not let them enter matching until Mobile has
            # independently read the same long suffix below.
            if (
                not audit_label.startswith("稳健圆心展开 Server 分带")
                and audit_label != "圆章章类型横向分带"
            ):
                audit.audit_texts.extend(current_audit_texts)
            audit.audit_variant_texts[audit_label] = current_audit_texts
        except Exception:
            continue


def _reconstruct_server_audit_evidence(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
    audit: SealAuditEvidence,
    candidate: dict,
) -> None:
    """Apply reconstruction and company-conflict guards without inventing text."""
    audit.audit_texts = _dedupe(audit.audit_texts)
    audit.robust_server_texts = _dedupe(
        [
            text
            for label, values in audit.audit_variant_texts.items()
            if label.startswith("稳健圆心展开 Server 分带")
            for text in values
        ]
    )
    audit.robust_shared_suffix = (
        _shared_long_organization_suffix(
            request.requirement,
            audit.robust_mobile_texts,
            audit.robust_server_texts,
        )
        # Only meaningful when the bands were read by a different tier from the
        # audit model; otherwise this would be a model corroborating itself.
        if route.audit_band_backend
        else ""
    )
    if audit.robust_shared_suffix:
        audit.audit_texts.append(audit.robust_shared_suffix)
        audit.audit_variant_texts["稳健圆心跨模型共同长后缀（无前缀补写）"] = [
            audit.robust_shared_suffix
        ]
    reconstructed_business_acceptance = (
        _reconstruct_business_acceptance_from_audit(
            request.requirement, audit.audit_texts
        )
    )
    if reconstructed_business_acceptance:
        audit.audit_texts.append(reconstructed_business_acceptance)
        audit.audit_variant_texts["同章区公司片段重组（无字符补写）"] = [
            reconstructed_business_acceptance
        ]
    reconstructed_exact_company_stamp = ""
    if not route.preliminary_company_conflict:
        reconstructed_exact_company_stamp = (
            _reconstruct_exact_company_stamp_from_region(
                request.requirement, audit.audit_texts
            )
        )
    if reconstructed_exact_company_stamp:
        audit.audit_texts.append(reconstructed_exact_company_stamp)
        audit.audit_variant_texts["同章区完整公司与章型重组（无字符补写）"] = [
            reconstructed_exact_company_stamp
        ]
    audit.reconstructed_one_error_type = (
        _reconstruct_one_error_round_type_band(
            request.requirement,
            candidate["evidence"],
            audit.round_type_band_rows,
        )
        if not route.preliminary_company_conflict
        else ""
    )
    if audit.reconstructed_one_error_type:
        audit.audit_texts.append(audit.reconstructed_one_error_type)
        audit.audit_variant_texts["完整公司 + 圆章章型单字纠错"] = [
            audit.reconstructed_one_error_type
        ]
    audit.reconstructed_partitioned_service = (
        _reconstruct_partitioned_service_organization(
            request.requirement, audit.audit_variant_texts
        )
        if audit.dense_partitioned_candidate
        else ""
    )
    if audit.reconstructed_partitioned_service:
        audit.audit_texts.append(audit.reconstructed_partitioned_service)
        audit.audit_variant_texts["圆章不同分带精确互补重组（无字符补写）"] = [
            audit.reconstructed_partitioned_service
        ]
    audit.combined_audit = combine_region_texts(audit.audit_texts)
    audit.server_audit_used_for_matching = bool(
        not route.preliminary_company_conflict or route.clipped_prefix_server_recheck
    )
    # A larger model must not erase contradictory evidence
    # from the regular local route.  In the reviewed
    # ``大连允华`` versus required ``大连北华`` sample, Mobile
    # reads the actual complete company while Server changes
    # the single discriminating glyph to the required one.
    # Keep Server output visible, but audit-only, whenever the
    # pre-audit evidence already contains a complete near-name
    # conflict.
    if audit.server_audit_used_for_matching:
        collection.overlapping_server_audits.append(
            {
                "index": candidate["index"],
                "region": candidate["region"],
                "texts": list(audit.audit_texts),
            }
        )
        collection.texts.extend(audit.audit_texts)
        if len(audit.audit_texts) >= 2 and audit.combined_audit:
            collection.texts.append(audit.combined_audit)


def _record_server_audit_artifact(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
    audit: SealAuditEvidence,
    candidate: dict,
) -> None:
    """Retain all URLs, OCR variants, confidence and acceptance explanations."""
    if candidate["index"] < len(collection.artifacts):
        collection.artifacts[candidate["index"]].update(
            server_audit_backend=backend_label(route.audit_backend or ""),
            server_audit_mode=route.audit_mode,
            server_audit_independent=bool(
                route.audit_backend and route.audit_backend != request.ocr_backend
            ),
            server_audit_text=" | ".join(audit.audit_texts),
            server_audit_combined_text=audit.combined_audit,
            server_audit_used_for_matching=(audit.server_audit_used_for_matching),
            server_audit_rejection_reason=(
                "常规模型识别到完整但不同的公司全称，"
                "Server 结果仅供人工复核"
                if not audit.server_audit_used_for_matching
                else ""
            ),
            server_audit_conflict_override_reason=(
                "常规模型另有恰好缺失首字的期望公司核心，"
                "允许颜色隔离 Server 证据参与最终复核"
                if route.clipped_prefix_server_recheck
                else ""
            ),
            server_audit_variants=[
                {
                    "preprocessing": label,
                    "ocr_texts": values,
                }
                for label, values in audit.audit_variant_texts.items()
            ],
            unwrapped_band_urls=[
                f"{request.artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                for path in audit.unwrapped_band_paths
                if path.is_file()
            ],
            partitioned_service_reconstructed_text=(
                audit.reconstructed_partitioned_service
            ),
            partitioned_service_acceptance_note=(
                (
                    "不同颜色安全圆章分带分别逐字读到互补组织片段，"
                    "无字符补写，允许参与匹配"
                    if audit.reconstructed_partitioned_service
                    else "未在不同圆章分带中读到两个精确互补片段，"
                    "保持待复核"
                )
                if audit.dense_partitioned_candidate
                else ""
            ),
            round_type_band_url=(
                f"{request.artifact_url_prefix.rstrip('/')}/seals/"
                f"{audit.round_type_band.name}"
                if audit.round_type_band is not None and audit.round_type_band.is_file()
                else ""
            ),
            round_type_band_text=" | ".join(
                row.text for row in audit.round_type_band_rows if row.text
            ),
            round_type_band_confidences=[
                round(float(row.confidence), 3)
                for row in audit.round_type_band_rows
                if row.text
            ],
            round_type_band_reconstructed_text=(
                audit.reconstructed_one_error_type
            ),
            round_type_band_acceptance_note=(
                "同章区已有完整公司；Server 横向分带以至少"
                "75% 置信度读到等长章型，仅一个汉字替换且完整"
                "保留‘专用章’，允许透明纠错"
                if audit.reconstructed_one_error_type
                else "未同时满足完整公司、无冲突、等长单字差异及"
                "75% 置信度，保持待复核"
            ),
        )
        if audit.robust_unwrapped is not None:
            collection.artifacts[candidate["index"]].update(
                robust_unwrapped_url=(
                    f"{request.artifact_url_prefix.rstrip('/')}/seals/"
                    f"{audit.robust_unwrapped.name}"
                ),
                robust_unwrapped_band_urls=[
                    f"{request.artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                    for path in audit.robust_band_paths
                    if path.is_file()
                ],
                robust_mobile_backend=backend_label(route.audit_band_backend or ""),
                robust_mobile_text=" | ".join(audit.robust_mobile_texts),
                robust_mobile_variants=audit.robust_mobile_variants,
                robust_server_text=" | ".join(audit.robust_server_texts),
                robust_shared_suffix=audit.robust_shared_suffix,
                robust_bounds_acceptance_note=(
                    "Mobile 与 Server 独立分带共同读到长组织后缀；"
                    "只采用实际文字，不补写缺失地名"
                    if audit.robust_shared_suffix
                    else "稳健边界未形成跨模型共同长后缀，保持待复核"
                ),
            )


def _combine_overlapping_server_evidence(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
) -> None:
    """Combine independently read fragments only for the guarded overlap route."""
    overlapping_repair_text = (
        _reconstruct_overlapping_repair_stamp(
            request.requirement, collection.overlapping_server_audits
        )
        if route.overlapping_repair_route
        else ""
    )
    if overlapping_repair_text:
        collection.texts.append(overlapping_repair_text)
        for entry in collection.overlapping_server_audits:
            artifact_index = int(entry.get("index", -1))
            if 0 <= artifact_index < len(collection.artifacts):
                collection.artifacts[artifact_index].update(
                    overlapping_region_reconstructed_text=(
                        overlapping_repair_text
                    ),
                    overlapping_region_acceptance_note=(
                        "两枚同色收货客户章显著重叠；Server 在不同"
                        "章区读到互补的精确公司分片及维修专用章，"
                        "按实识别字符重组"
                    ),
                )
