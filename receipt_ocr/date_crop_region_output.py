"""Confirm far-lower dates and publish region observations and artifacts."""

from __future__ import annotations
from pathlib import Path
from .parser import parse_date
from .ocr_types import TextObservation
from .date_evidence import (
    _cross_model_far_lower_complementary_date,
    _cross_model_far_lower_strict_date,
    _repeated_strict_date_across_variants,
    _save_right_padded_date_line,
)
from .date_crop_state import DateCropRun, DateCropRegion


def _confirm_far_lower_region(run: DateCropRun, region: DateCropRegion) -> None:
    region.evidence.far_lower_cross_model_date = None
    region.evidence.far_lower_server_variants: list[dict] = []
    region.evidence.far_lower_padded_line: Path | None = None
    region.evidence.far_lower_padded_variants: list[dict] = []
    region.evidence.far_lower_cross_model_mode = ""
    region.evidence.far_lower_confirmed_rows: list[TextObservation] = []
    if (
        region.crop_key == "far_lower"
        and region.audit_only
        and run.ocr_backend == "vision"
        and run.secondary_ocr_backend == "paddle"
        and _repeated_strict_date_across_variants(
            region.evidence.secondary_ocr_variants
        )
        is not None
    ):
        far_lower_server_rows: list[TextObservation] = []
        for preprocessing, candidate in (
            ("窄日期行原图", region.images.line_raw),
            ("窄日期行去印章色", region.images.line_color_clean),
        ):
            try:
                current_server_rows = run.services.recognize_text(
                    candidate,
                    backend="paddle_server",
                    min_text_height=0.012,
                )
            except Exception:
                current_server_rows = []
            far_lower_server_rows.extend(current_server_rows)
            region.evidence.far_lower_server_variants.append(
                {
                    "preprocessing": preprocessing,
                    "ocr_texts": [row.text for row in current_server_rows],
                }
            )
        region.evidence.far_lower_cross_model_date = _cross_model_far_lower_strict_date(
            region.evidence.secondary_ocr_variants,
            region.evidence.far_lower_server_variants,
            [
                row.text
                for row in (
                    run.output
                    + region.evidence.variant_rows
                    + region.evidence.secondary_raw_rows
                    + region.evidence.line_rows
                    + far_lower_server_rows
                )
            ],
        )
        if region.evidence.far_lower_cross_model_date is not None:
            region.evidence.far_lower_cross_model_mode = "strict_full_date"
        if region.evidence.far_lower_cross_model_date is None:
            region.evidence.far_lower_padded_line = (
                Path(run.temp_dir) / "date-far_lower-line-right-padded.png"
            )
            _save_right_padded_date_line(
                region.images.line_raw, region.evidence.far_lower_padded_line
            )
            padded_rows_by_backend: dict[str, list[TextObservation]] = {}
            for padded_backend in ("paddle", "paddle_server"):
                try:
                    padded_rows = run.services.recognize_text(
                        region.evidence.far_lower_padded_line,
                        backend=padded_backend,
                        min_text_height=0.012,
                    )
                except Exception:
                    padded_rows = []
                padded_rows_by_backend[padded_backend] = padded_rows
                region.evidence.far_lower_padded_variants.append(
                    {
                        "preprocessing": (
                            "右侧补白日期行 "
                            + run.services.backend_label(padded_backend)
                        ),
                        "ocr_texts": [row.text for row in padded_rows],
                    }
                )
            complementary = _cross_model_far_lower_complementary_date(
                region.evidence.secondary_ocr_variants,
                region.evidence.far_lower_server_variants,
                [row.text for row in padded_rows_by_backend.get("paddle", [])],
                [row.text for row in padded_rows_by_backend.get("paddle_server", [])],
                [
                    row.text
                    for row in (
                        run.output
                        + region.evidence.variant_rows
                        + region.evidence.secondary_raw_rows
                        + region.evidence.line_rows
                        + far_lower_server_rows
                    )
                ],
            )
            if complementary is not None:
                region.evidence.far_lower_cross_model_date = complementary
                region.evidence.far_lower_cross_model_mode = (
                    "right_padding_complementary"
                )
        if region.evidence.far_lower_cross_model_date is not None:
            normalized = (
                f"{region.evidence.far_lower_cross_model_date.year}年"
                f"{region.evidence.far_lower_cross_model_date.month}月"
                f"{region.evidence.far_lower_cross_model_date.day}日"
            )
            mobile_confidence = max(
                (
                    row.confidence
                    for row in region.evidence.secondary_raw_rows
                    if parse_date(row.text)
                    == region.evidence.far_lower_cross_model_date
                ),
                default=0.72,
            )
            server_confidence = max(
                (
                    row.confidence
                    for row in far_lower_server_rows
                    if parse_date(row.text)
                    == region.evidence.far_lower_cross_model_date
                ),
                default=0.72,
            )
            for confidence in (mobile_confidence, server_confidence):
                region.evidence.far_lower_confirmed_rows.append(
                    TextObservation(
                        text=normalized,
                        confidence=float(confidence),
                        x=region.images.x
                        + region.images.line_box[0] * region.images.width,
                        y=region.images.y
                        + region.images.line_box[1] * region.images.height,
                        width=region.images.line_box[2] * region.images.width,
                        height=region.images.line_box[3] * region.images.height,
                    )
                )
        region.evidence.line_variants.append(
            {
                "preprocessing": ("远下方 Mobile 完整区域 + Server 窄日期行复核"),
                "ocr_texts": [
                    text
                    for item in (
                        region.evidence.far_lower_server_variants
                        + region.evidence.far_lower_padded_variants
                    )
                    for text in item["ocr_texts"]
                ],
                "accepted_texts": (
                    [region.evidence.far_lower_cross_model_date.isoformat()]
                    if region.evidence.far_lower_cross_model_date
                    else []
                ),
                "acceptance_note": (
                    "Mobile 完整区域与补白日期行读到同一严格日期；"
                    "Server 原日期行确认年月、补白日期行确认月日"
                    if region.evidence.far_lower_cross_model_mode
                    == "right_padding_complementary"
                    else (
                        "Mobile 与 Server 在不同几何、各两种预处理上"
                        "读到同一严格四位日期"
                        if region.evidence.far_lower_cross_model_date
                        else "未形成跨模型、跨几何的重复严格日期，保持待复核"
                    )
                ),
            }
        )


def _publish_date_region(run: DateCropRun, region: DateCropRegion) -> None:
    run.date_crop_entries.append(
        {
            "crop_key": region.crop_key,
            "tight": region.tight,
            "audit_only": region.audit_only,
            "line_rows": list(region.evidence.line_rows),
            "line_raw": region.images.line_raw,
            "line_table_clean_upscaled": region.images.line_table_clean_upscaled,
            "line_positioned_frame_clean": region.images.line_positioned_frame_clean,
            "line_autocontrast_upscaled": region.images.line_autocontrast_upscaled,
            "line_max_channel_upscaled": region.images.line_max_channel_upscaled,
            "line_otsu_upscaled": region.images.line_otsu_upscaled,
            "line_white_standardized": region.images.line_white_standardized,
            "line_box": region.images.line_box,
            "region_box": (
                region.images.x,
                region.images.y,
                region.images.width,
                region.images.height,
            ),
            "line_variants": region.evidence.line_variants,
        }
    )
    artifact_variant_rows = list(region.evidence.variant_rows)
    audit_region_rows = list(region.evidence.variant_rows) + list(
        region.evidence.secondary_raw_rows
    )
    audit_line_rows = list(region.evidence.line_rows)
    if region.audit_only:
        region.evidence.variant_rows = []
        region.evidence.secondary_variant_rows = []
        region.evidence.accepted_line_rows = []
    lx, ly, lw, lh = region.images.line_box
    region.evidence.variant_rows.extend(
        TextObservation(
            text=row.text,
            confidence=row.confidence,
            x=lx + row.x * lw,
            y=ly + row.y * lh,
            width=row.width * lw,
            height=row.height * lh,
        )
        for row in region.evidence.accepted_line_rows
    )
    region.evidence.variant_rows.extend(region.evidence.secondary_variant_rows)
    normalized_rows = [
        type(row)(
            text=row.text,
            confidence=row.confidence,
            x=region.images.x + row.x * region.images.width,
            y=region.images.y + row.y * region.images.height,
            width=row.width * region.images.width,
            height=row.height * region.images.height,
        )
        for row in region.evidence.variant_rows
    ]
    # The tight crop is the only geometry used for the live date decision.
    # Keep its normalized observations on the artifact so the stage can
    # select them without confusing overlapping wide-crop coordinates.
    if region.crop_key == "tight" and not region.audit_only:
        run.primary_rows = list(normalized_rows)
    run.output.extend(normalized_rows)
    if region.audit_only:
        # Only strict four-digit dates survive this evidence path.
        # Keep the candidate visible but capped below every
        # automatic-decision threshold.
        for row in audit_region_rows:
            if parse_date(row.text) is None:
                continue
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=min(0.35, row.confidence),
                    x=region.images.x + row.x * region.images.width,
                    y=region.images.y + row.y * region.images.height,
                    width=row.width * region.images.width,
                    height=row.height * region.images.height,
                )
            )
        for row in audit_line_rows:
            if parse_date(row.text) is None:
                continue
            run.output.append(
                type(row)(
                    text=row.text,
                    confidence=min(0.35, row.confidence),
                    x=region.images.x + (lx + row.x * lw) * region.images.width,
                    y=region.images.y + (ly + row.y * lh) * region.images.height,
                    width=row.width * lw * region.images.width,
                    height=row.height * lh * region.images.height,
                )
            )
        run.output.extend(region.evidence.far_lower_confirmed_rows)
    if run.artifact_dir:
        prefix = run.artifact_url_prefix.rstrip("/")
        run.artifacts.append(
            {
                "variant": {
                    "tight": "紧凑区域",
                    "wide": "宽区域",
                    "lower": "下方扩展区域",
                    "far_lower": "远下方手写日期复核区域",
                    "deep_lower": "页面底部手写日期复核区域",
                }[region.crop_key],
                "ocr_backend": run.services.backend_label(run.ocr_backend),
                "secondary_ocr_backend": (
                    run.services.backend_label(run.secondary_ocr_backend)
                    if run.secondary_ocr_backend
                    else ""
                ),
                "original_url": f"{prefix}/date/{region.images.raw.name}",
                "color_clean_url": f"{prefix}/date/{region.images.color_clean.name}",
                "line_clean_url": f"{prefix}/date/{region.images.crop.name}",
                "date_line_original_url": f"{prefix}/date/{region.images.line_raw.name}",
                "date_line_color_clean_url": f"{prefix}/date/{region.images.line_color_clean.name}",
                "date_line_table_clean_url": f"{prefix}/date/{region.images.line_table_clean.name}",
                "date_line_positioned_frame_clean_url": (
                    f"{prefix}/date/{region.images.line_positioned_frame_clean.name}"
                    if region.images.line_positioned_frame_clean is not None
                    and region.images.line_positioned_frame_clean.is_file()
                    else ""
                ),
                "date_line_table_clean_upscaled_url": (
                    f"{prefix}/date/{region.images.line_table_clean_upscaled.name}"
                    if region.images.line_table_clean_upscaled is not None
                    and region.images.line_table_clean_upscaled.is_file()
                    else ""
                ),
                "date_line_autocontrast_upscaled_url": (
                    f"{prefix}/date/{region.images.line_autocontrast_upscaled.name}"
                    if region.images.line_autocontrast_upscaled is not None
                    and region.images.line_autocontrast_upscaled.is_file()
                    else ""
                ),
                "date_line_max_channel_upscaled_url": (
                    f"{prefix}/date/{region.images.line_max_channel_upscaled.name}"
                    if region.images.line_max_channel_upscaled is not None
                    and region.images.line_max_channel_upscaled.is_file()
                    else ""
                ),
                "date_line_otsu_upscaled_url": (
                    f"{prefix}/date/{region.images.line_otsu_upscaled.name}"
                    if region.images.line_otsu_upscaled is not None
                    and region.images.line_otsu_upscaled.is_file()
                    else ""
                ),
                "date_line_white_standardized_url": (
                    f"{prefix}/date/{region.images.line_white_standardized.name}"
                    if region.images.line_white_standardized is not None
                    and region.images.line_white_standardized.is_file()
                    else ""
                ),
                "upper_date_line_original_url": (
                    f"{prefix}/date/{region.images.upper_line_raw.name}"
                    if region.images.upper_line_raw is not None
                    else ""
                ),
                "upper_date_line_color_clean_url": (
                    f"{prefix}/date/{region.images.upper_line_color_clean.name}"
                    if region.images.upper_line_color_clean is not None
                    else ""
                ),
                "ocr_texts": [row.text for row in artifact_variant_rows],
                "decision_rows": (
                    [row.to_dict() for row in run.primary_rows]
                    if region.crop_key == "tight" and not region.audit_only
                    else []
                ),
                "ocr_variants": region.evidence.ocr_variants,
                "secondary_ocr_variants": region.evidence.secondary_ocr_variants,
                "date_line_ocr_backend": (
                    run.services.backend_label(region.evidence.line_backend)
                    if region.evidence.line_backend
                    else ""
                ),
                "date_line_ocr_variants": region.evidence.line_variants,
                "date_line_display_ocr_variants": (
                    region.evidence.display_line_variants
                ),
                "date_line_component_candidate": region.evidence.component_candidate,
                "date_line_component_confidence": (
                    region.evidence.component_candidate_confidence
                ),
                "far_lower_cross_model_candidate": (
                    region.evidence.far_lower_cross_model_date.isoformat()
                    if region.evidence.far_lower_cross_model_date
                    else ""
                ),
                "far_lower_server_backend": (
                    run.services.backend_label("paddle_server")
                    if region.evidence.far_lower_server_variants
                    else ""
                ),
                "far_lower_server_variants": (
                    region.evidence.far_lower_server_variants
                ),
                "far_lower_padded_line_url": (
                    f"{prefix}/date/{region.evidence.far_lower_padded_line.name}"
                    if region.evidence.far_lower_padded_line is not None
                    and region.evidence.far_lower_padded_line.is_file()
                    else ""
                ),
                "far_lower_padded_variants": (
                    region.evidence.far_lower_padded_variants
                ),
                "far_lower_cross_model_mode": (
                    region.evidence.far_lower_cross_model_mode
                ),
            }
        )
