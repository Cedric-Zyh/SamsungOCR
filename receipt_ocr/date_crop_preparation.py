"""Prepare region images and conservative date-line derivatives."""

from __future__ import annotations
from pathlib import Path
from PIL import Image
from .date_evidence import _save_date_upper_line_crop
from .date_crop_state import DateCropRun, DateCropRegion


def _save_positioned_outer_frame_clean(
    source: str | Path,
    destination: str | Path,
) -> None:
    """Remove only the date-row frame, leaving interior digit strokes intact.

    The existing table-line view is deliberately conservative and can leave
    the row's outer rules visible.  This audit-only view uses the geometry of
    the date line: long horizontal rules in the upper/lower bands and long
    vertical rules at the image edges are treated as frame pixels.  Interior
    vertical strokes (notably a handwritten ``1`` before ``日``) are not
    removed merely because they are vertical.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np

        with Image.open(source) as opened:
            rgb = opened.convert("RGB")
            array = np.asarray(rgb).copy()
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        ink = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY_INV)[1]
        height, width = ink.shape

        # Locate the entire rule (including pale antialiasing), then protect
        # columns with nearby ink on BOTH sides of that rule. Delete only the
        # unprotected parts of the narrow band, never reconstruct handwriting.
        band_mask = np.zeros_like(ink)
        protected = np.zeros_like(ink)
        pale_ink = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)[1]
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(30, width // 8), 1)
        )
        horizontal = cv2.morphologyEx(pale_ink, cv2.MORPH_OPEN, horizontal_kernel)
        row_density = np.count_nonzero(horizontal, axis=1) / max(1, width)
        upper_limit = round(height * 0.38)
        lower_start = round(height * 0.52)
        for start, stop in ((0, upper_limit), (lower_start, height)):
            candidate_rows = np.flatnonzero(row_density[start:stop] >= 0.45) + start
            if candidate_rows.size:
                groups = np.split(
                    candidate_rows,
                    np.where(np.diff(candidate_rows) > 1)[0] + 1,
                )
                for group in groups:
                    if not group.size:
                        continue
                    first = max(0, int(group[0]) - 1)
                    last = min(height, int(group[-1]) + 2)
                    depth = max(3, round(height * 0.045))
                    # Allow a slight horizontal shift for slanted 1/7 strokes.
                    radius = max(2, round(height * 0.025))
                    kernel = np.ones((1, radius * 2 + 1), np.uint8)
                    above = (ink[max(0, first - depth):first] > 0).any(axis=0)
                    below = (ink[last:min(height, last + depth)] > 0).any(axis=0)
                    above = cv2.dilate(above.astype(np.uint8)[None, :], kernel)[0]
                    below = cv2.dilate(below.astype(np.uint8)[None, :], kernel)[0]
                    crossing = cv2.dilate(
                        (above & below)[None, :], np.ones((1, 3), np.uint8)
                    )[0] > 0
                    columns = np.flatnonzero(horizontal[group].any(axis=0))
                    if not columns.size:
                        continue
                    left = max(0, int(columns[0]) - 1)
                    right = min(width, int(columns[-1]) + 2)
                    band_mask[first:last, left:right] = 255
                    protected[first:last, crossing] = 255

        # Remove a vertical frame only when it is at the extreme left/right
        # edge.  The common interior vertical stroke before ``日`` is kept.
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, max(12, height // 5))
        )
        vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, vertical_kernel)
        edge_mask = np.zeros_like(vertical)
        # The printed ``日`` cell can leave its right border several pixels
        # inside the crop edge.  A wider right-only band removes that border
        # while leaving the handwritten day and the separator before ``日``.
        edge_width = max(2, round(width * 0.08))
        edge_mask[:, :edge_width] = vertical[:, :edge_width]
        edge_start = max(0, width - edge_width)
        edge_mask[:, edge_start:] = vertical[:, edge_start:]

        mask = cv2.dilate(
            cv2.bitwise_or(band_mask, edge_mask),
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
        )
        # Apply protection last, so mask dilation cannot eat the crossings.
        mask[protected > 0] = 0
        cleaned = array.copy()
        cleaned[mask > 0] = 255
        Image.fromarray(cleaned).save(destination)
    except Exception:
        # The extra view is for visual audit only; preserve the pipeline if
        # optional image tooling is unavailable.
        with Image.open(source) as opened:
            opened.convert("RGB").save(destination)


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
