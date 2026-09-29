"""Read Secondary seal evidence, reconstruct supported text and record its provenance."""

from __future__ import annotations

from pathlib import Path

from receipt_ocr.providers.catalog import backend_label
from receipt_ocr.runtime.utils import _dedupe
from receipt_ocr.recognition.seal.ocr.interface import read_line, read_text
from receipt_ocr.recognition.seal.reading_policy import variant_for_reader
from receipt_ocr.recognition.seal.postprocess.rules import (
    _reconstruct_business_acceptance_from_secondary,
    _reconstruct_exact_company_stamp_from_region,
    _reconstruct_one_error_round_type_band,
    _reconstruct_overlapping_repair_stamp,
    _reconstruct_partitioned_service_organization,
    _shared_long_organization_suffix,
    combine_region_texts,
)
from receipt_ocr.recognition.seal.contracts import (
    SealRequest,
    SealReadCollection,
    SecondaryReadPlan,
    SecondaryReadEvidence,
)


def _read_secondary_evidence(
    secondary: SecondaryReadEvidence, route: SecondaryReadPlan
) -> None:
    """Keep secondary-only model readings separate from accepted matching text.

    The provider comes from the resolved policy rather than a literal, so the
    call can never be denied by the request scope without the caller knowing.
    """
    backend = route.secondary_backend
    if not backend:
        return
    variant = variant_for_reader(backend)
    secondary.secondary_variant_texts: dict[str, list[str]] = {}
    for secondary_label, secondary_path in secondary.secondary_paths:
        if not secondary_path or not Path(secondary_path).is_file():
            continue
        try:
            # Every generated horizontal band is already a single logical
            # text line.  Use recognition-only inference for it, including
            # rotated type bands and the three unwrapped strips; re-running
            # detection on a band adds latency and can split one row.
            robust_band = secondary_label.startswith("稳健圆心展开 分带 OCR")
            line_input = secondary_label == "矩形编号章数字行" or "分带" in secondary_label
            line_backend = route.secondary_band_backend if robust_band else backend
            if line_input:
                secondary_rows = read_line(
                    secondary_path, provider=line_backend
                )
            else:
                secondary_rows = read_text(
                    secondary_path,
                    backend=line_backend,
                    min_text_height=0.012,
                )
            current_secondary_texts = [
                row.text for row in secondary_rows if row.text
            ]
            if robust_band:
                secondary.robust_line_texts.extend(current_secondary_texts)
                secondary.robust_line_variants.append({
                    "preprocessing": secondary_label,
                    "ocr_texts": current_secondary_texts,
                })
            if secondary_label in {
                "圆章章类型横向分带",
                "旋正后圆章章型横向分带",
                "椭圆章章类型横向分带",
                "旋正后椭圆章章型横向分带",
            }:
                secondary.round_type_band_rows = list(secondary_rows)
            # The robust-bound bands are a cross-model route.
            # Keep Secondary-only readings visible for secondary but
            # do not let them enter matching until 分带 OCR has
            # independently read the same long suffix below.
            if (
                not robust_band
                and not secondary_label.startswith("稳健圆心展开 Secondary 分带")
                and secondary_label not in {
                    "圆章章类型横向分带",
                    "旋正后圆章章型横向分带",
                    "椭圆章章类型横向分带",
                    "旋正后椭圆章章型横向分带",
                }
            ):
                secondary.secondary_texts.extend(current_secondary_texts)
            secondary.secondary_variant_texts[secondary_label] = current_secondary_texts
        except Exception:
            continue


def _reconstruct_secondary_evidence(
    request: SealRequest,
    collection: SealReadCollection,
    route: SecondaryReadPlan,
    secondary: SecondaryReadEvidence,
    candidate: dict,
) -> None:
    """Apply reconstruction and company-conflict guards without inventing text."""
    secondary.secondary_texts = _dedupe(secondary.secondary_texts)
    secondary.robust_reader_texts = _dedupe(
        [
            text
            for label, values in secondary.secondary_variant_texts.items()
            if label.startswith("稳健圆心展开 Secondary 分带")
            for text in values
        ]
    )
    secondary.robust_shared_suffix = (
        _shared_long_organization_suffix(
            request.requirement,
            secondary.robust_line_texts,
            secondary.robust_reader_texts,
        )
        # Only meaningful when the bands were read by a different tier from the
        # secondary reader; otherwise this would be a model corroborating itself.
        if route.secondary_band_backend
        else ""
    )
    if secondary.robust_shared_suffix:
        secondary.secondary_texts.append(secondary.robust_shared_suffix)
        secondary.secondary_variant_texts["稳健圆心跨模型共同长后缀（无前缀补写）"] = [
            secondary.robust_shared_suffix
        ]
    reconstructed_business_acceptance = (
        _reconstruct_business_acceptance_from_secondary(
            request.requirement, secondary.secondary_texts
        )
    )
    if reconstructed_business_acceptance:
        secondary.secondary_texts.append(reconstructed_business_acceptance)
        secondary.secondary_variant_texts["同章区公司片段重组（无字符补写）"] = [
            reconstructed_business_acceptance
        ]
    reconstructed_exact_company_stamp = ""
    if not route.preliminary_company_conflict:
        reconstructed_exact_company_stamp = (
            _reconstruct_exact_company_stamp_from_region(
                request.requirement, secondary.secondary_texts
            )
        )
    if reconstructed_exact_company_stamp:
        secondary.secondary_texts.append(reconstructed_exact_company_stamp)
        secondary.secondary_variant_texts["同章区完整公司与章型重组（无字符补写）"] = [
            reconstructed_exact_company_stamp
        ]
    secondary.reconstructed_one_error_type = (
        _reconstruct_one_error_round_type_band(
            request.requirement,
            candidate["evidence"],
            secondary.round_type_band_rows,
        )
        if not route.preliminary_company_conflict
        else ""
    )
    if secondary.reconstructed_one_error_type:
        secondary.secondary_texts.append(secondary.reconstructed_one_error_type)
        secondary.secondary_variant_texts["完整公司 + 圆章章型单字纠错"] = [
            secondary.reconstructed_one_error_type
        ]
    secondary.reconstructed_partitioned_service = (
        _reconstruct_partitioned_service_organization(
            request.requirement, secondary.secondary_variant_texts
        )
        if secondary.dense_partitioned_candidate
        else ""
    )
    if secondary.reconstructed_partitioned_service:
        secondary.secondary_texts.append(secondary.reconstructed_partitioned_service)
        secondary.secondary_variant_texts["圆章不同分带精确互补重组（无字符补写）"] = [
            secondary.reconstructed_partitioned_service
        ]
    secondary.combined_secondary_text = combine_region_texts(secondary.secondary_texts)
    secondary.secondary_read_used_for_matching = bool(
        not route.preliminary_company_conflict or route.clipped_prefix_secondary_recheck
    )
    # A larger model must not erase contradictory evidence
    # from the regular local route.  In the reviewed
    # ``大连允华`` versus required ``大连北华`` sample, 分带 OCR
    # reads the actual complete company while Secondary changes
    # the single discriminating glyph to the required one.
    # Keep Secondary output visible, but secondary-only, whenever the
    # pre-secondary evidence already contains a complete near-name
    # conflict.
    if secondary.secondary_read_used_for_matching:
        collection.overlapping_secondary_reads.append(
            {
                "index": candidate["index"],
                "region": candidate["region"],
                "texts": list(secondary.secondary_texts),
            }
        )
        collection.texts.extend(secondary.secondary_texts)
        if len(secondary.secondary_texts) >= 2 and secondary.combined_secondary_text:
            collection.texts.append(secondary.combined_secondary_text)


def _record_secondary_artifact(
    request: SealRequest,
    collection: SealReadCollection,
    route: SecondaryReadPlan,
    secondary: SecondaryReadEvidence,
    candidate: dict,
) -> None:
    """Retain all URLs, OCR variants, confidence and acceptance explanations."""
    if candidate["index"] < len(collection.artifacts):
        collection.artifacts[candidate["index"]].update(
            secondary_read_backend=backend_label(route.secondary_backend or ""),
            secondary_read_mode=route.secondary_mode,
            secondary_read_independent=bool(
                route.secondary_backend and route.secondary_backend != request.ocr_backend
            ),
            secondary_read_text=" | ".join(secondary.secondary_texts),
            secondary_read_combined_text=secondary.combined_secondary_text,
            secondary_read_used_for_matching=(secondary.secondary_read_used_for_matching),
            secondary_read_rejection_reason=(
                "常规模型识别到完整但不同的公司全称，"
                "Secondary 结果仅供人工复核"
                if not secondary.secondary_read_used_for_matching
                else ""
            ),
            secondary_read_conflict_override_reason=(
                "常规模型另有恰好缺失首字的期望公司核心，"
                "允许颜色隔离 Secondary 证据参与最终复核"
                if route.clipped_prefix_secondary_recheck
                else ""
            ),
            secondary_read_variants=[
                {
                    "preprocessing": label,
                    "ocr_texts": values,
                }
                for label, values in secondary.secondary_variant_texts.items()
            ],
            ellipse_normalized_url=(
                f"{request.artifact_url_prefix.rstrip('/')}/seals/"
                f"{Path(candidate['ellipse_normalized']).name}"
                if candidate.get("ellipse_normalized")
                and Path(candidate["ellipse_normalized"]).is_file()
                else ""
            ),
            unwrapped_band_urls=[
                f"{request.artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                for path in secondary.unwrapped_band_paths
                if path.is_file()
            ],
            partitioned_service_reconstructed_text=(
                secondary.reconstructed_partitioned_service
            ),
            partitioned_service_acceptance_note=(
                (
                    "不同颜色安全圆章分带分别逐字读到互补组织片段，"
                    "无字符补写，允许参与匹配"
                    if secondary.reconstructed_partitioned_service
                    else "未在不同圆章分带中读到两个精确互补片段，"
                    "保持待复核"
                )
                if secondary.dense_partitioned_candidate
                else ""
            ),
            round_type_band_url=(
                f"{request.artifact_url_prefix.rstrip('/')}/seals/"
                f"{secondary.round_type_band.name}"
                if secondary.round_type_band is not None and secondary.round_type_band.is_file()
                else ""
            ),
            round_type_band_text=" | ".join(
                row.text for row in secondary.round_type_band_rows if row.text
            ),
            round_type_band_confidences=[
                round(float(row.confidence), 3)
                for row in secondary.round_type_band_rows
                if row.text
            ],
            round_type_band_reconstructed_text=(
                secondary.reconstructed_one_error_type
            ),
            round_type_band_acceptance_note=(
                "同章区已有完整公司；Secondary 横向分带以至少"
                "75% 置信度读到等长章型，仅一个汉字替换且完整"
                "保留‘专用章’，允许透明纠错"
                if secondary.reconstructed_one_error_type
                else "未同时满足完整公司、无冲突、等长单字差异及"
                "75% 置信度，保持待复核"
            ),
        )
        if secondary.robust_unwrapped is not None:
            collection.artifacts[candidate["index"]].update(
                robust_unwrapped_url=(
                    f"{request.artifact_url_prefix.rstrip('/')}/seals/"
                    f"{secondary.robust_unwrapped.name}"
                ),
                robust_unwrapped_band_urls=[
                    f"{request.artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                    for path in secondary.robust_band_paths
                    if path.is_file()
                ],
                robust_line_backend=backend_label(route.secondary_band_backend or ""),
                robust_line_text=" | ".join(secondary.robust_line_texts),
                robust_line_variants=secondary.robust_line_variants,
                robust_reader_text=" | ".join(secondary.robust_reader_texts),
                robust_shared_suffix=secondary.robust_shared_suffix,
                robust_bounds_acceptance_note=(
                    "分带 OCR 与 Secondary 独立分带共同读到长组织后缀；"
                    "只采用实际文字，不补写缺失地名"
                    if secondary.robust_shared_suffix
                    else "稳健边界未形成跨模型共同长后缀，保持待复核"
                ),
            )


def _combine_overlapping_secondary_evidence(
    request: SealRequest,
    collection: SealReadCollection,
    route: SecondaryReadPlan,
) -> None:
    """Combine independently read fragments only for the guarded overlap route."""
    overlapping_repair_text = (
        _reconstruct_overlapping_repair_stamp(
            request.requirement, collection.overlapping_secondary_reads
        )
        if route.overlapping_repair_route
        else ""
    )
    if overlapping_repair_text:
        collection.texts.append(overlapping_repair_text)
        for entry in collection.overlapping_secondary_reads:
            artifact_index = int(entry.get("index", -1))
            if 0 <= artifact_index < len(collection.artifacts):
                collection.artifacts[artifact_index].update(
                    overlapping_region_reconstructed_text=(
                        overlapping_repair_text
                    ),
                    overlapping_region_acceptance_note=(
                        "两枚同色收货客户章显著重叠；Secondary 在不同"
                        "章区读到互补的精确公司分片及维修专用章，"
                        "按实识别字符重组"
                    ),
            )

