"""Prepare region images and conservative date-line derivatives."""

from __future__ import annotations
from pathlib import Path
from PIL import Image
from receipt_ocr.recognition.date.preprocess.crops import _save_date_upper_line_crop
from receipt_ocr.recognition.date.contracts import DateCropRun, DateCropRegion


from receipt_ocr.recognition.date.preprocess.frame import _save_positioned_outer_frame_clean


def _prepare_date_region(run: DateCropRun, region: DateCropRegion) -> None:
    region.images.crop = Path(run.temp_dir) / f"date-{region.crop_key}.png"
    region.images.raw = Path(run.temp_dir) / f"date-{region.crop_key}-original.jpg"
    region.images.color_clean = (
        Path(run.temp_dir) / f"date-{region.crop_key}-color-clean.png"
    )
    region.images.x, region.images.y, region.images.width, region.images.height = (
        run.services.save_receipt_date_crop(
            run.source,
            region.images.crop,
            run.anchor_y + region.anchor_shift,
            tight=region.tight,
            raw_destination=region.images.raw,
            color_clean_destination=region.images.color_clean,
            # Far-below handwritten audit notes are often centered
            # under the stamp instead of aligned to the printed date
            # cell. Widen only this audit crop; its evidence remains
            # capped below every automatic-decision threshold.
            left=0.48 if region.crop_key in {"far_lower", "deep_lower"} else None,
        )
    )
    region.images.line_raw = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-original.jpg"
    )
    region.images.line_color_clean = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-color-clean.png"
    )
    region.images.line_table_clean = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-table-clean.png"
    )
    is_lower = region.crop_key in {"lower", "far_lower", "deep_lower"}
    region.images.line_box = run.services.save_date_line_crop(
        region.images.raw, region.images.line_raw, tight=region.tight, lower=is_lower
    )
    run.services.save_date_line_crop(
        region.images.color_clean,
        region.images.line_color_clean,
        tight=region.tight,
        lower=is_lower,
    )
    # Keep a safe thresholded line only for the internal table-line OCR
    # candidate.  The displayed frame-clean image below is made from the
    # aggressively red-suppressed preview, so stamp color does not remain in
    # the user-facing derivative.
    run.services.save_date_line_crop(
        region.images.crop,
        region.images.line_table_clean,
        tight=region.tight,
        lower=is_lower,
    )
    region.images.line_positioned_frame_clean = (
        Path(run.temp_dir)
        / f"date-{region.crop_key}-line-positioned-frame-clean.png"
    )
    _save_positioned_outer_frame_clean(
        region.images.line_color_clean,
        region.images.line_positioned_frame_clean,
    )
    # Keep only the three user-facing date-line images. The former enlarged,
    # channel, Otsu and white-canvas derivatives were audit noise and are no
    # longer generated.
    region.images.line_table_clean_upscaled = None
    region.images.line_autocontrast_upscaled = None
    region.images.line_max_channel_upscaled = None
    region.images.line_otsu_upscaled = None
    region.images.line_white_standardized = None
    region.images.upper_line_raw = None
    region.images.upper_line_color_clean = None
    region.images.upper_line_box = None
    if region.crop_key == "wide":
        # A few customers write ``2025.10.20`` in the upper
        # signature/盖章 row instead of the printed date row below.
        # Preserve a separate visible crop so this path can be
        # audited without enlarging the normal date-line evidence.
        region.images.upper_line_raw = (
            Path(run.temp_dir) / "date-wide-upper-line-original.jpg"
        )
        region.images.upper_line_color_clean = (
            Path(run.temp_dir) / "date-wide-upper-line-color-clean.png"
        )
        region.images.upper_line_box = _save_date_upper_line_crop(
            region.images.raw, region.images.upper_line_raw
        )
        _save_date_upper_line_crop(
            region.images.color_clean, region.images.upper_line_color_clean
        )

    from .manifest import prepare_manifest
    region.prepared = prepare_manifest(region)
