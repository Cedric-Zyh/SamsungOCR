"""Prepare only the derivative images authorized by each seal audit route."""

from __future__ import annotations

from pathlib import Path

from .image_processing import (
    save_round_seal_type_band,
    save_unwrapped_seal,
    save_unwrapped_seal_bands,
)
from .ocr_backends import recognize_text
from .ocr_types import TextObservation
from .recognition_utils import _dedupe
from .seal_crop_types import SealCropRequest, SealAuditRoute, SealAuditEvidence


def _prepare_server_audit_images(
    request: SealCropRequest,
    route: SealAuditRoute,
    audit: SealAuditEvidence,
    candidate: dict,
    audit_position: int,
) -> None:
    """Build audit derivatives and independent Mobile bands in the original order."""
    audit.dense_partitioned_candidate = bool(
        route.dense_partitioned_service_organization
        and float(candidate.get("pixel_ratio", 0)) >= 0.20
    )
    audit.audit_texts: list[str] = []
    audit.round_type_band: Path | None = None
    audit.round_type_band_rows: list[TextObservation] = []
    audit.robust_unwrapped: Path | None = None
    audit.robust_band_paths: list[Path] = []
    audit.robust_mobile_texts: list[str] = []
    audit.robust_mobile_variants: list[dict] = []
    # Both derivatives contain colored ink only.  The
    # color-preserving view often recovers a rectangular
    # stamp-type row, while the polar/normalized view recovers
    # the curved company name. They remain safe from printed
    # requirement self-matching because neutral form text was
    # removed before either image reached the Server model.
    audit.audit_paths = [
        ("保留章色白底图", candidate["color_isolated"]),
        ("圆章/矩形校正图", candidate["unwrapped"]),
    ]
    if (
        request.artifact_dir
        and audit_position == 0
        and not candidate["rectangular"]
        and route.explicit_stamp_type
        and not route.preliminary_company_conflict
        and float(route.preliminary.get("score", 0)) >= 0.72
        and float(route.preliminary.get("company_score", 0)) >= 0.70
    ):
        audit.round_type_band = Path(candidate["color_isolated"]).with_name(
            f"seal-{candidate['index']}-round-type-band.png"
        )
        try:
            save_round_seal_type_band(
                candidate["color_isolated"], audit.round_type_band
            )
        except Exception:
            audit.round_type_band = None
        if audit.round_type_band is not None:
            audit.audit_paths.append(("圆章章类型横向分带", audit.round_type_band))
    if (
        request.artifact_dir
        and audit_position == 0
        and route.robust_round_shop
        and route.audit_band_backend
        and not candidate["rectangular"]
    ):
        audit.robust_unwrapped = Path(candidate["unwrapped"]).with_name(
            f"seal-{candidate['index']}-" "unwrapped-robust-bounds.png"
        )
        try:
            used_robust_bounds = save_unwrapped_seal(
                request.source,
                audit.robust_unwrapped,
                candidate["region"],
                robust_bounds=True,
            )
        except Exception:
            used_robust_bounds = False
        if used_robust_bounds:
            try:
                audit.robust_band_paths = save_unwrapped_seal_bands(
                    audit.robust_unwrapped,
                    audit.robust_unwrapped.with_name(
                        f"seal-{candidate['index']}-"
                        "unwrapped-robust-band"
                    ),
                )
            except Exception:
                audit.robust_band_paths = []
        else:
            audit.robust_band_paths = []
            try:
                audit.robust_unwrapped.unlink(missing_ok=True)
            except Exception:
                pass
            audit.robust_unwrapped = None
        for band_index, band_path in enumerate(audit.robust_band_paths, start=1):
            current_mobile_texts: list[str] = []
            try:
                current_mobile_texts = [
                    row.text
                    for row in recognize_text(
                        band_path,
                        backend=route.audit_band_backend,
                        min_text_height=0.012,
                    )
                    if row.text
                ]
            except Exception:
                current_mobile_texts = []
            audit.robust_mobile_texts.extend(current_mobile_texts)
            audit.robust_mobile_variants.append(
                {
                    "preprocessing": (
                        f"稳健圆心展开 Mobile 分带 {band_index}"
                    ),
                    "ocr_texts": current_mobile_texts,
                }
            )
            audit.audit_paths.append(
                (
                    f"稳健圆心展开 Server 分带 {band_index}",
                    band_path,
                )
            )
        audit.robust_mobile_texts = _dedupe(audit.robust_mobile_texts)
    audit.unwrapped_band_paths: list[Path] = []
    # Reviewed shallow oval seal ``7286691342`` is the only
    # current truth sample in this narrow score window.  The
    # three angular shifts are readable independently by the
    # Server model, but shrink too far when stacked into one
    # tall contact sheet.  Split only the best circular
    # candidate for a pure company-name requirement, after
    # regular OCR has already established a very strong,
    # non-conflicting company prefix.  Stamp-type requirements
    # and near-name conflicts remain outside this route.
    should_audit_unwrapped_bands = bool(
        request.artifact_dir
        and audit_position == 0
        and not candidate["rectangular"]
        and not route.preliminary_company_conflict
        and (
            (
                route.company_only_requirement
                and 0.72 <= float(route.preliminary.get("score", 0)) < 0.80
                and float(route.preliminary.get("company_score", 0)) >= 0.90
            )
            or audit.dense_partitioned_candidate
        )
    )
    if should_audit_unwrapped_bands:
        try:
            audit.unwrapped_band_paths = save_unwrapped_seal_bands(
                candidate["unwrapped"],
                Path(candidate["unwrapped"]).with_name(
                    f"seal-{candidate['index']}-unwrapped-band"
                ),
            )
        except Exception:
            audit.unwrapped_band_paths = []
        audit.audit_paths.extend(
            (f"圆章展开分带 {index}", path)
            for index, path in enumerate(audit.unwrapped_band_paths, start=1)
        )
    if route.numbered_service_stamp and candidate.get("code_line"):
        audit.audit_paths.append(
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
        and (route.explicit_stamp_type or audit.dense_partitioned_candidate)
        and (
            route.overlapping_repair_route
            or route.branch_stamp_rotation_route
            or audit.dense_partitioned_candidate
            or (
                float(route.preliminary.get("score", 0)) >= 0.72
                and float(route.preliminary.get("company_score", 0)) >= 0.70
            )
        )
    ):
        audit.audit_paths.append(
            (
                "圆章展开 180°",
                candidate["unwrapped_rotated"],
            )
        )
        if candidate.get("color_isolated_rotations") is not None:
            audit.audit_paths.append(
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
            label != "保留章色旋转对照图" for label, _path in audit.audit_paths
        )
    ):
        audit.audit_paths.append(
            (
                "保留章色旋转对照图",
                candidate["color_isolated_rotations"],
            )
        )
