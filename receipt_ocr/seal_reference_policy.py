"""Apply reference seal evidence before the shared final decision."""


def apply_reference_evidence(result: dict, matcher) -> dict:
    """Promote only a broad match to an exact human-truth seal reference."""
    if result.get("recognition_config") is not None or result.get('seal_check', {}).get('dual_check'):
        return result
    evidence = matcher.match(result)
    if "reference_filename" not in evidence:
        return result
    seal_check = result.get("seal_check") or {}
    seal_check["visual_reference_match"] = evidence
    route = str(evidence.get("route") or "")
    consensus_routes = {
        "multi_reference_consensus",
        "high_purity_multi_reference_consensus",
        "chromatic_crop_multi_reference_consensus",
        "color_mask_multi_reference_consensus",
        "strong_prefix_color_mask_multi_reference_consensus",
    }
    candidate_index = int(
        evidence.get("consensus_candidate_index", -1)
        if route in consensus_routes
        else evidence.get("candidate_index", -1)
    )
    for artifact in result.get("processing_artifacts", {}).get("seals", []):
        if int(artifact.get("index", -2)) == candidate_index:
            artifact["visual_reference_match"] = evidence
            break
    if not evidence.get("accepted"):
        return result

    confidence = float(evidence.get("confidence", 0.90))
    seal_check.update(
        {
            "ocr_only_status": seal_check.get("status", ""),
            "ocr_only_reliable": bool(seal_check.get("reliable")),
            "ocr_only_score": float(seal_check.get("score", 0)),
            "status": "匹配",
            "message": (
                "OCR 文字不完整，但章面与同签章要求的人工真值阳性参考章"
                + (
                    "在两份独立样单中形成一致几何证据"
                    if route in consensus_routes
                    else (
                        "的彩色墨迹形成整体几何一致"
                        if route == "color_mask_geometry"
                        else (
                            "同时形成整体彩色墨迹与大面积局部几何一致"
                            if route == "color_mask_sift_geometry"
                            else (
                                "去除稀疏彩色扫描噪点后形成高纯度大面积几何一致"
                                if route == "trimmed_chromatic_single_reference"
                                else "形成大面积几何一致"
                            )
                        )
                    )
                )
            ),
            "score": round(max(float(seal_check.get("score", 0)), confidence), 3),
            "confidence": confidence,
            "reliable": True,
            "match_basis": (
                "人工真值参考章 + SIFT/RANSAC 多参考一致"
                if route == "multi_reference_consensus"
                else (
                    "人工真值参考章 + SIFT/RANSAC 多参考高纯度一致"
                    if route == "high_purity_multi_reference_consensus"
                    else (
                        "人工真值参考章 + 章色稳健裁剪/SIFT 多参考一致"
                        if route == "chromatic_crop_multi_reference_consensus"
                        else (
                            "人工真值参考章 + 整体彩色墨迹多参考一致"
                            if route == "color_mask_multi_reference_consensus"
                            else (
                                "人工真值参考章 + 强文字前缀/章色多参考一致"
                                if route
                                == "strong_prefix_color_mask_multi_reference_consensus"
                                else (
                                    "人工真值参考章 + 彩色墨迹整体几何一致"
                                    if route == "color_mask_geometry"
                                    else (
                                        "人工真值参考章 + 彩色墨迹/SIFT 联合几何一致"
                                        if route == "color_mask_sift_geometry"
                                        else (
                                            "人工真值参考章 + 稀疏章色噪点裁剪/SIFT 高纯度一致"
                                            if route
                                            == "trimmed_chromatic_single_reference"
                                            else (
                                                "人工真值参考章 + SIFT/RANSAC 高支持度微覆盖抖动"
                                                if route
                                                == "high_support_minor_coverage"
                                                else (
                                                    "人工真值参考章 + SIFT/RANSAC 超高支持度局部覆盖"
                                                    if route
                                                    == "ultra_support_partial_coverage"
                                                    else (
                                                        "人工真值参考章 + SIFT/RANSAC 高内点率几何一致"
                                                        if route
                                                        == "high_ratio_single_reference"
                                                        else "人工真值参考章 + SIFT/RANSAC 大面积几何一致"
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            ),
            "backend": (
                str(seal_check.get("backend") or "本地 OCR") + " + 本地人工真值参考章"
            ),
        }
    )
    result["seal_check"] = seal_check
    reasons = [
        reason
        for reason in result.get("review_reasons", [])
        if reason != "印章内容无法可靠判断"
    ]
    result["review_reasons"] = reasons
    result["stage_review_reasons"] = [
        reason
        for reason in result.get("stage_review_reasons", [])
        if reason != "印章内容无法可靠判断"
    ]
    return result
