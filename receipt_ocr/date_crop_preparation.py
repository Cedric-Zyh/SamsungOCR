"""Prepare region images and conservative date-line derivatives."""

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageChops, ImageOps
from .date_evidence import _date_slot_white_canvas, _save_date_upper_line_crop
from .date_crop_state import DateCropRun, DateCropRegion


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
    # ``crop`` is the high-resolution, color-suppressed image
    # with long table rules removed by ``save_receipt_date_crop``.
    # Keep an exact line derivative as a visible intermediate
    # artifact and a conservative OCR candidate.  It is useful
    # when a handwritten digit touches a table border, but this
    # transform may also erase part of a digit; consequently only
    # a strict four-digit date from it may enter machine evidence.
    run.services.save_date_line_crop(
        region.images.crop,
        region.images.line_table_clean,
        tight=region.tight,
        lower=is_lower,
    )
    region.images.line_table_clean_upscaled = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-table-clean-upscaled.png"
    )
    try:
        with Image.open(region.images.line_table_clean) as table_clean_image:
            table_clean_image.resize(
                (
                    table_clean_image.width * 3,
                    table_clean_image.height * 3,
                ),
                Image.Resampling.LANCZOS,
            ).save(region.images.line_table_clean_upscaled)
    except Exception:
        region.images.line_table_clean_upscaled = None
    region.images.line_autocontrast_upscaled = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-autocontrast-upscaled.png"
    )
    try:
        with Image.open(region.images.line_raw) as raw_line_image:
            autocontrast_line = ImageOps.autocontrast(
                ImageOps.grayscale(raw_line_image), cutoff=1
            )
            autocontrast_line.resize(
                (
                    autocontrast_line.width * 3,
                    autocontrast_line.height * 3,
                ),
                Image.Resampling.LANCZOS,
            ).save(region.images.line_autocontrast_upscaled)
    except Exception:
        region.images.line_autocontrast_upscaled = None
    region.images.line_max_channel_upscaled = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-max-channel-upscaled.png"
    )
    try:
        with Image.open(region.images.line_raw) as raw_line_image:
            red, green, blue = raw_line_image.convert("RGB").split()
            max_channel = ImageChops.lighter(red, ImageChops.lighter(green, blue))
            max_channel = ImageOps.autocontrast(max_channel, cutoff=1)
            max_channel.resize(
                (
                    max_channel.width * 3,
                    max_channel.height * 3,
                ),
                Image.Resampling.LANCZOS,
            ).save(region.images.line_max_channel_upscaled)
    except Exception:
        region.images.line_max_channel_upscaled = None
    region.images.line_otsu_upscaled = (
        Path(run.temp_dir) / f"date-{region.crop_key}-line-otsu-upscaled.png"
    )
    try:
        # Otsu keeps a dark handwritten digit that can disappear
        # in grayscale interpolation.  Generate the view for the
        # review UI, but only a later cross-model guard may turn
        # its OCR into a low-confidence candidate.
        import cv2
        import numpy as np

        with Image.open(region.images.line_raw) as raw_line_image:
            gray_array = np.asarray(ImageOps.grayscale(raw_line_image))
            otsu = cv2.threshold(
                gray_array,
                0,
                255,
                cv2.THRESH_BINARY | cv2.THRESH_OTSU,
            )[1]
            Image.fromarray(otsu).resize(
                (
                    raw_line_image.width * 3,
                    raw_line_image.height * 3,
                ),
                Image.Resampling.NEAREST,
            ).save(region.images.line_otsu_upscaled)
    except Exception:
        region.images.line_otsu_upscaled = None
    region.images.line_white_standardized = (
        Path(run.temp_dir)
        / f"date-{region.crop_key}-line-original-white-standardized.png"
    )
    try:
        with Image.open(region.images.line_raw) as raw_line_image:
            _date_slot_white_canvas(raw_line_image.convert("RGB")).save(
                region.images.line_white_standardized
            )
    except Exception:
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
