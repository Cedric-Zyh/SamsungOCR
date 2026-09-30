"""Prepare only the derivative images authorized by each seal secondary route."""

from __future__ import annotations

from pathlib import Path

from receipt_ocr.imaging.bands import save_round_seal_type_band, save_unwrapped_seal_bands

from receipt_ocr.imaging.unwrap import save_unwrapped_seal
from receipt_ocr.recognition.seal.contracts import SealRequest, SecondaryReadPlan, SecondaryReadEvidence


def _prepare_secondary_images(
    request: SealRequest,
    route: SecondaryReadPlan,
    secondary: SecondaryReadEvidence,
    candidate: dict,
    read_position: int,
) -> None:
    """Build secondary derivatives and independent 分带 OCR bands in the original order."""
    secondary.dense_partitioned_candidate = bool(
        route.dense_partitioned_service_organization
        and float(candidate.get("pixel_ratio", 0)) >= 0.20
    )
    secondary.secondary_texts: list[str] = []
    secondary.round_type_band: Path | None = None
    secondary.round_type_band_rows = []
    secondary.robust_unwrapped: Path | None = None
    secondary.robust_band_paths: list[Path] = []
    secondary.robust_line_texts = []
    secondary.robust_line_variants = []
    # Both derivatives contain colored ink only.  The
    # color-preserving view often recovers a rectangular
    # stamp-type row, while the polar/normalized view recovers
    # the curved company name. They remain safe from printed
    # requirement self-matching because neutral form text was
    # removed before either image reached the Secondary model.
    color_source = candidate.get("color_isolated_oriented") or candidate["color_isolated"]
    ellipse_normalized = candidate.get("ellipse_normalized")
    normalized_label = (
        "椭圆环形文字展开图"
        if candidate.get("elliptical") or candidate.get("shape") == "椭圆"
        else "圆章/矩形校正图"
    )
    secondary.secondary_paths = [
        (
            "按章型文字旋正后的保留章色图"
            if candidate.get("color_isolated_oriented")
            else "保留章色白底图",
            color_source,
        ),
        (normalized_label, candidate["unwrapped"]),
    ]
    if ellipse_normalized and Path(ellipse_normalized).is_file():
        secondary.secondary_paths.insert(1, ("椭圆拉伸校正图", Path(ellipse_normalized)))
    if (
        request.artifact_dir
        and read_position == 0
        and not candidate["rectangular"]
        and route.explicit_stamp_type
        and not route.preliminary_company_conflict
        and float(route.preliminary.get("score", 0)) >= 0.72
        and float(route.preliminary.get("company_score", 0)) >= 0.70
    ):
        secondary.round_type_band = Path(candidate["color_isolated"]).with_name(
            f"seal-{candidate['index']}-round-type-band.png"
        )
        try:
            using_ellipse_normalized = bool(
                (candidate.get("elliptical") or candidate.get("shape") == "椭圆")
                and ellipse_normalized
                and Path(ellipse_normalized).is_file()
            )
            type_band_source = (
                Path(ellipse_normalized)
                if using_ellipse_normalized
                else color_source
            )
            save_round_seal_type_band(
                type_band_source,
                secondary.round_type_band,
                orientation_aligned=(
                    using_ellipse_normalized
                    or candidate.get("color_isolated_oriented") is not None
                ),
                focus_box=(
                    None
                    if using_ellipse_normalized
                    else (candidate.get("orientation") or {}).get("oriented_type_row_box")
                ),
            )
        except Exception:
            secondary.round_type_band = None
        if secondary.round_type_band is not None:
            band_label = (
                "旋正后椭圆章章型横向分带"
                if candidate.get("elliptical") or candidate.get("shape") == "椭圆"
                else "旋正后圆章章型横向分带"
            ) if candidate.get("color_isolated_oriented") else (
                "椭圆章章类型横向分带"
                if candidate.get("elliptical") or candidate.get("shape") == "椭圆"
                else "圆章章类型横向分带"
            )
            secondary.secondary_paths.append(
                (
                    band_label,
                    secondary.round_type_band,
                )
            )
    if (
        request.artifact_dir
        and read_position == 0
        and route.robust_round_shop
        and route.secondary_band_backend
        and not candidate["rectangular"]
    ):
        secondary.robust_unwrapped = Path(candidate["unwrapped"]).with_name(
            f"seal-{candidate['index']}-" "unwrapped-robust-bounds.png"
        )
        try:
            used_robust_bounds = save_unwrapped_seal(
                request.source,
                secondary.robust_unwrapped,
                candidate["region"],
                robust_bounds=True,
                exclude_boxes=candidate.get("ring_type_mask_boxes", []),
            )
        except Exception:
            used_robust_bounds = False
        if used_robust_bounds:
            try:
                secondary.robust_band_paths = save_unwrapped_seal_bands(
                    secondary.robust_unwrapped,
                    secondary.robust_unwrapped.with_name(
                        f"seal-{candidate['index']}-"
                        "unwrapped-robust-band"
                    ),
                )
            except Exception:
                secondary.robust_band_paths = []
        else:
            secondary.robust_band_paths = []
            try:
                secondary.robust_unwrapped.unlink(missing_ok=True)
            except Exception:
                pass
            secondary.robust_unwrapped = None
        for band_index, band_path in enumerate(secondary.robust_band_paths, start=1):
            secondary.secondary_paths.append(
                (
                    f"稳健圆心展开 分带 OCR 分带 {band_index}",
                    band_path,
                )
            )
    secondary.unwrapped_band_paths: list[Path] = []
    # Reviewed shallow oval seal ``7286691342`` is the only
    # current truth sample in this narrow score window.  The
    # three angular shifts are readable independently by the
    # Secondary model, but shrink too far when stacked into one
    # tall contact sheet.  Split only the best circular
    # candidate for a pure company-name requirement, after
    # regular OCR has already established a very strong,
    # non-conflicting company prefix.  Stamp-type requirements
    # and near-name conflicts remain outside this route.
    should_read_unwrapped_bands = bool(
        request.artifact_dir
        and read_position == 0
        and not candidate["rectangular"]
        and not route.preliminary_company_conflict
        and (
            (
                route.company_only_requirement
                and 0.72 <= float(route.preliminary.get("score", 0)) < 0.80
                and float(route.preliminary.get("company_score", 0)) >= 0.90
            )
            or secondary.dense_partitioned_candidate
        )
    )
    if should_read_unwrapped_bands:
        try:
            secondary.unwrapped_band_paths = save_unwrapped_seal_bands(
                candidate["unwrapped"],
                Path(candidate["unwrapped"]).with_name(
                    f"seal-{candidate['index']}-unwrapped-band"
                ),
            )
        except Exception:
            secondary.unwrapped_band_paths = []
        secondary.secondary_paths.extend(
            (f"圆章展开分带 {index}", path)
            for index, path in enumerate(secondary.unwrapped_band_paths, start=1)
        )
    if route.numbered_service_stamp and candidate.get("code_line"):
        secondary.secondary_paths.append(
            (
                "矩形编号章数字行",
                candidate["code_line"],
            )
        )
    # Only ask the large model to read the opposite-facing
    # round-stamp text when regular evidence already contains
    # a strong company name but lacks the required stamp type.
    # This keeps batch latency bounded and cannot self-match
    # against the black printed requirement row because the
    # derivative contains colored ink only.
    if (
        not candidate["rectangular"]
        and candidate.get("unwrapped_rotated") is not None
        and (route.explicit_stamp_type or secondary.dense_partitioned_candidate)
        and (
            route.overlapping_repair_route
            or route.branch_stamp_rotation_route
            or secondary.dense_partitioned_candidate
            or (
                float(route.preliminary.get("score", 0)) >= 0.72
                and float(route.preliminary.get("company_score", 0)) >= 0.70
            )
        )
    ):
        secondary.secondary_paths.append(
            (
                "圆章展开 180°",
                candidate["unwrapped_rotated"],
            )
        )
        if candidate.get("color_isolated_rotations") is not None:
            secondary.secondary_paths.append(
                (
                    "保留章色旋转对照图",
                    candidate["color_isolated_rotations"],
                )
            )
    # For the reviewed local Samsung service-center template,
    # brand/place/type face different directions around the
    # ring.  Include the already generated color-preserving
    # rotation sheet after the narrow strong-color route; it
    # contains no black printed requirement text.
    if (
        route.fragmented_local_service_center
        and candidate.get("color_isolated_rotations") is not None
        and all(
            label != "保留章色旋转对照图" for label, _path in secondary.secondary_paths
        )
    ):
        secondary.secondary_paths.append(
            (
                "保留章色旋转对照图",
                candidate["color_isolated_rotations"],
            )
        )
