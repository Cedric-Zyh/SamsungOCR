"""Ordered visual-reference route selection and audit result assembly."""

from __future__ import annotations

from .seal_reference_confidence import (
    _reference_confidence,
)
from .seal_reference_consensus import (
    _best_chromatic_crop_consensus,
    _best_color_mask_consensus,
    _best_consensus,
    _best_high_purity_consensus,
    _best_prefix_color_mask_consensus,
)
from .seal_reference_constants import (
    _reference_thresholds,
)
from .seal_reference_gates import (
    _color_evidence_rank,
    _evidence_rank,
    _passes_bare_company_one_glyph_reference_gate,
    _passes_branded_station_gate,
    _passes_clipped_company_reference_gate,
    _passes_color_mask_gate,
    _passes_color_sift_gate,
    _passes_company_conflict_reference_gate,
    _passes_high_ratio_gate,
    _passes_high_support_gate,
    _passes_receiving_one_glyph_reference_gate,
    _passes_strict_gate,
    _passes_trimmed_chromatic_gate,
    _passes_ultra_support_gate,
)


def _decide_reference_match(
    seal_check: dict, requirement: str, best: dict,
    best_trimmed_chromatic: dict | None, best_color: dict | None,
    all_evidence: list[dict],
) -> dict:
    strict_accepted = _passes_strict_gate(best)
    high_ratio_accepted = _passes_high_ratio_gate(best)
    high_support_accepted = _passes_high_support_gate(best)
    ultra_support_accepted = _passes_ultra_support_gate(best)
    branded_station_accepted = _passes_branded_station_gate(
        best,
        requirement=requirement,
        recognized=str(seal_check.get("recognized") or ""),
    )
    company_conflict_reference_accepted = (
        _passes_company_conflict_reference_gate(best, seal_check)
    )
    receiving_one_glyph_reference_accepted = (
        _passes_receiving_one_glyph_reference_gate(best, seal_check)
    )
    bare_company_one_glyph_reference_accepted = (
        _passes_bare_company_one_glyph_reference_gate(best, seal_check)
    )
    clipped_company_reference_accepted = (
        _passes_clipped_company_reference_gate(best, seal_check)
    )
    trimmed_chromatic_accepted = bool(
        best_trimmed_chromatic is not None
        and _passes_trimmed_chromatic_gate(best_trimmed_chromatic)
    )
    consensus = _best_consensus(all_evidence)
    consensus_accepted = bool(consensus.get("accepted"))
    high_purity_consensus = _best_high_purity_consensus(all_evidence)
    high_purity_consensus_accepted = bool(
        high_purity_consensus.get("accepted")
    )
    chromatic_consensus = _best_chromatic_crop_consensus(all_evidence)
    chromatic_consensus_accepted = bool(
        chromatic_consensus.get("accepted")
    )
    color_mask_consensus = _best_color_mask_consensus(
        all_evidence, seal_check
    )
    color_mask_consensus_accepted = bool(
        color_mask_consensus.get("accepted")
    )
    prefix_color_consensus = _best_prefix_color_mask_consensus(
        all_evidence, seal_check
    )
    prefix_color_consensus_accepted = bool(
        prefix_color_consensus.get("accepted")
    )
    color_mask_accepted = bool(
        best_color is not None and _passes_color_mask_gate(best_color)
    )
    color_sift_accepted = bool(
        best_color is not None and _passes_color_sift_gate(best_color)
    )
    active_consensus = (
        consensus if consensus_accepted
        else high_purity_consensus
        if high_purity_consensus_accepted
        else chromatic_consensus
        if chromatic_consensus_accepted
        else color_mask_consensus
        if color_mask_consensus_accepted
        else prefix_color_consensus
        if prefix_color_consensus_accepted
        else consensus
    )
    if (
        consensus_accepted or high_purity_consensus_accepted
        or chromatic_consensus_accepted or color_mask_consensus_accepted
        or prefix_color_consensus_accepted
    ) and not (
        strict_accepted or high_ratio_accepted or high_support_accepted
        or ultra_support_accepted or branded_station_accepted
        or trimmed_chromatic_accepted
    ):
        consensus_candidate = int(active_consensus["candidate_index"])
        leading_reference = str(
            active_consensus["matches"][0]["reference_filename"]
        )
        best = max(
            (
                item for item in all_evidence
                if int(item.get("candidate_index", -1))
                == consensus_candidate
                and str(item.get("reference_filename") or "")
                == leading_reference
            ),
            key=(
                _color_evidence_rank
                if (
                    color_mask_consensus_accepted
                    or prefix_color_consensus_accepted
                )
                else _evidence_rank
            ),
        )
    if (
        trimmed_chromatic_accepted
        and not (
            strict_accepted or high_ratio_accepted
            or high_support_accepted or ultra_support_accepted
            or branded_station_accepted
        )
    ):
        best = best_trimmed_chromatic
    if (
        (color_mask_accepted or color_sift_accepted)
        and not (
            strict_accepted or high_ratio_accepted
            or high_support_accepted or ultra_support_accepted
            or branded_station_accepted
            or trimmed_chromatic_accepted
            or consensus_accepted or high_purity_consensus_accepted
            or chromatic_consensus_accepted
            or color_mask_consensus_accepted
            or prefix_color_consensus_accepted
        )
    ):
        best = best_color
    generic_routes_allowed = not bool(
        seal_check.get("company_conflict")
    )
    accepted = (
        company_conflict_reference_accepted
        or receiving_one_glyph_reference_accepted
        or bare_company_one_glyph_reference_accepted
        or clipped_company_reference_accepted
    ) or (
        generic_routes_allowed and (
            strict_accepted or high_ratio_accepted
            or high_support_accepted or ultra_support_accepted
            or branded_station_accepted
            or trimmed_chromatic_accepted
            or consensus_accepted or high_purity_consensus_accepted
            or chromatic_consensus_accepted
            or color_mask_consensus_accepted
            or prefix_color_consensus_accepted
            or color_mask_accepted or color_sift_accepted
        )
    )
    route = (
        "company_conflict_ultra_reference"
        if company_conflict_reference_accepted
        else "receiving_one_glyph_reference"
        if receiving_one_glyph_reference_accepted
        else "bare_company_one_glyph_reference"
        if bare_company_one_glyph_reference_accepted
        else "clipped_prefix_company_ultra_reference"
        if clipped_company_reference_accepted
        else "rejected" if not generic_routes_allowed
        else "strict_single_reference" if strict_accepted
        else "high_ratio_single_reference" if high_ratio_accepted
        else "high_support_minor_coverage" if high_support_accepted
        else "ultra_support_partial_coverage" if ultra_support_accepted
        else "branded_station_single_reference"
        if branded_station_accepted
        else "trimmed_chromatic_single_reference"
        if trimmed_chromatic_accepted
        else "multi_reference_consensus" if consensus_accepted
        else "high_purity_multi_reference_consensus"
        if high_purity_consensus_accepted
        else "chromatic_crop_multi_reference_consensus"
        if chromatic_consensus_accepted
        else "color_mask_multi_reference_consensus"
        if color_mask_consensus_accepted
        else "strong_prefix_color_mask_multi_reference_consensus"
        if prefix_color_consensus_accepted
        else "color_mask_geometry" if color_mask_accepted
        else "color_mask_sift_geometry" if color_sift_accepted
        else "rejected"
    )
    best["accepted"] = accepted
    best["route"] = route
    best["consensus_candidate_index"] = active_consensus.get(
        "candidate_index", -1
    )
    best["consensus_reference_count"] = int(
        active_consensus.get("reference_count", 0)
    )
    best["consensus_matches"] = active_consensus.get("matches", [])
    if route == "chromatic_crop_multi_reference_consensus":
        best["regular_geometry"] = {
            key: best.get(key, 0)
            for key in (
                "good_matches", "homography_inliers", "inlier_ratio",
                "candidate_coverage", "reference_coverage",
            )
        }
        for key in (
            "good_matches", "homography_inliers", "inlier_ratio",
            "candidate_coverage", "reference_coverage",
        ):
            best[key] = best.get(f"chromatic_{key}", 0)
    elif route == "trimmed_chromatic_single_reference":
        best["regular_geometry"] = {
            key: best.get(key, 0)
            for key in (
                "good_matches", "homography_inliers", "inlier_ratio",
                "candidate_coverage", "reference_coverage",
            )
        }
        for key in (
            "good_matches", "homography_inliers", "inlier_ratio",
            "candidate_coverage", "reference_coverage",
        ):
            best[key] = best.get(f"trimmed_chromatic_{key}", 0)
    best["thresholds"] = _reference_thresholds()
    best["confidence"] = (
        _reference_confidence(best, route, active_consensus)
        if accepted else 0.0
    )
    best["reason"] = (
        (
            "当前章保留售后专用章和公司片段，并与同要求人工真值阳性章形成双表示超高支持度几何一致"
            if company_conflict_reference_accepted else
            "当前章只出现一个公司字冲突，且收货专用章文字与同要求人工真值阳性章的双表示大面积几何一致"
            if receiving_one_glyph_reference_accepted else
            "纯公司章只出现一个公司字冲突，并与同要求人工真值阳性章形成双SIFT及整体彩色墨迹一致"
            if bare_company_one_glyph_reference_accepted else
            "当前章公司全称仅缺前缀，并与同要求人工真值阳性章形成双表示超高支持度几何一致"
            if clipped_company_reference_accepted else
            "与同签章要求的人工真值阳性章形成大面积几何一致"
            if strict_accepted else
            "与人工真值阳性章形成高内点率的大面积几何一致"
            if high_ratio_accepted else
            "与人工真值阳性章形成高支持度几何一致，覆盖差异仅为裁剪抖动"
            if high_support_accepted else
            "与人工真值阳性章形成超高支持度几何一致，局部覆盖不足源于遮挡或裁剪"
            if ultra_support_accepted else
            "当前章保留三星服务中心文字，并与同七位站号要求的人工真值阳性章形成大面积几何一致"
            if branded_station_accepted else
            "去除稀疏彩色扫描噪点后，与人工真值阳性章形成高纯度大面积几何一致"
            if trimmed_chromatic_accepted else
            "同一章区与至少两份独立人工真值阳性章形成几何一致"
            if consensus_accepted else
            "同一章区与两份独立人工真值阳性章形成高纯度几何一致"
            if high_purity_consensus_accepted else
            "排除黑色表格与签字噪声后，同一章区与两份独立人工真值阳性章一致"
            if chromatic_consensus_accepted else
            "同一纯公司章区域与两份独立人工真值阳性章形成整体彩色墨迹共识"
            if color_mask_consensus_accepted else
            "当前章保留签章要求的强连续前缀，且同一章区与两份独立人工真值阳性章形成章色和局部几何共识"
            if prefix_color_consensus_accepted else
            "与人工真值阳性章的彩色墨迹形成整体几何一致"
            if color_mask_accepted else
            "与人工真值阳性章同时形成整体彩色墨迹与大面积局部几何一致"
        )
        if accepted else
        "公司主体冲突且未达到超高参考几何联合门槛"
        if seal_check.get("company_conflict") else
        "视觉特征未达到人工真值参考章的严格几何门槛"
    )
    return best
