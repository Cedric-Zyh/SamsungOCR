"""Historical far-lower confirmation (inactive for the compact pipeline)."""
from pathlib import Path
from receipt_ocr.domain.parsing import parse_date
from receipt_ocr.domain.ocr import TextObservation
from .evidence import _cross_model_far_lower_complementary_date, _cross_model_far_lower_strict_date, _repeated_strict_date_across_variants
from ..preprocess.crops import _save_right_padded_date_line
from ..contracts import DateCropRun, DateCropRegion

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
                ("窄日期行去印章色", region.images.line_color_clean),
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
                region.images.line_color_clean, region.evidence.far_lower_padded_line
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

