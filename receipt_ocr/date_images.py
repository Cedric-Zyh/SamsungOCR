"""Prepare date lines and digit slots while preserving crop geometry."""

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageChops, ImageOps


def _save_date_line_crop(
    source: str | Path,
    destination: str | Path,
    *,
    tight: bool,
    lower: bool = False,
) -> tuple[float, float, float, float]:
    """Save the handwritten date row inside a date-region crop."""
    # Handwriting can rise across the upper table rule. Keep headroom above
    # the nominal date row: cutting at the rule erases digit ascenders before
    # either color suppression or recognition can recover them. The context
    # remains inside the date region, below most of the preceding cell.
    # The below-table variant has no table row above it. Preserve the full
    # glyph height and remove only the signature/mark area on its far left.
    if lower:
        left, top = 0.34, 0.0
    else:
        left, top = (0.22, 0.22) if tight else (0.28, 0.28)
    right, bottom = 0.995, 0.995
    with Image.open(source) as image:
        width, height = image.size
        cropped = image.crop(
            (
                round(left * width),
                round(top * height),
                round(right * width),
                round(bottom * height),
            )
        )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() in {".jpg", ".jpeg"}:
            cropped.convert("RGB").save(destination, quality=95)
        else:
            cropped.save(destination)
    return left, top, right - left, bottom - top


def _save_right_padded_date_line(
    source: str | Path,
    destination: str | Path,
) -> None:
    """Add white OCR context around a line clipped against the page edge."""
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        padded = ImageOps.expand(
            rgb,
            border=(
                round(rgb.width * 0.03),
                round(rgb.height * 0.08),
                round(rgb.width * 0.10),
                round(rgb.height * 0.08),
            ),
            fill="white",
        )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        padded.save(destination)


def _save_date_slot_views(
    source: str | Path,
    destination_dir: str | Path,
) -> dict[str, dict[str, Path]]:
    """Save auditable year and month/day views from the fixed date template.

    The Samsung form prints ``20 年 月 日`` around handwriting. Whole-line OCR
    often merges those glyphs with a stamp or the table border. These two
    overlapping views preserve the printed units while giving the recognizer
    enough horizontal context to avoid clipping a leading day digit.
    """
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    slots = {
        "year_full": (0.0, 0.52),
        # Context views used only when Server has already repeated one strict
        # full date across tight/wide geometries.  They retain the printed
        # units and the adjacent component so a handwritten ``9`` is not
        # mistaken for ``3`` by the overly wide generic month/day crop.
        "month_context": (0.36, 0.74),
        "day_context": (0.50, 0.985),
        # Handwriting may start immediately after the printed ``月`` and sit
        # farther left than the legacy digit-only slot. This view keeps the
        # handwritten day and right-hand unit while excluding most month ink.
        # It is used only behind a strict cross-model evidence prefilter.
        "day_digits_adaptive": (0.50, 0.80),
        # A thin handwritten month ``1`` is easily merged with the printed
        # ``月`` and following day. Preserve a digit-only audit view between
        # the printed year/month units; the two OCR models must still agree.
        "month_digits": (0.405, 0.515),
        "month_day": (0.30, 0.985),
        # The printed ``月`` and ``日`` delimit a stable day-only cell. Keep
        # enough padding for a narrow leading ``1`` without admitting either
        # printed unit, making a lost tens digit directly auditable.
        "day_digits": (0.66, 0.86),
        # Some scans place the printed ``日`` farther left.  This alternate
        # crop ends before that unit so connected ``16`` handwriting is not
        # classified together with the printed glyph. It is used only by the
        # missing-year-separator consensus route and needs four OCR cells.
        "day_digits_inner": (0.58, 0.78),
    }
    output: dict[str, dict[str, Path]] = {}
    with Image.open(source) as opened:
        rgb = opened.convert("RGB")
        red, green, blue = rgb.split()
        max_channel = ImageChops.lighter(red, ImageChops.lighter(green, blue))
        max_channel = ImageOps.autocontrast(max_channel, cutoff=1)
        for name, (left, right) in slots.items():
            box = (
                round(rgb.width * left),
                0,
                round(rgb.width * right),
                rgb.height,
            )
            raw_crop = rgb.crop(box)
            clean_crop = max_channel.crop(box)
            crop_red, crop_green, crop_blue = raw_crop.split()
            crop_first_clean = ImageOps.autocontrast(
                ImageChops.lighter(crop_red, ImageChops.lighter(crop_green, crop_blue)),
                cutoff=1,
            )
            raw_path = destination / f"date-slot-{name}-original.jpg"
            clean_path = destination / f"date-slot-{name}-max-channel.png"
            white_path = destination / f"date-slot-{name}-max-channel-white.png"
            crop_first_white_path = (
                destination / f"date-slot-{name}-crop-first-max-channel-white.png"
            )
            line_clean_path = (
                destination / f"date-slot-{name}-max-channel-line-clean.png"
            )
            raw_crop.save(raw_path, quality=95)
            clean_crop.resize(
                (
                    max(180, clean_crop.width * 3),
                    max(96, clean_crop.height * 3),
                ),
                Image.Resampling.LANCZOS,
            ).save(clean_path)
            try:
                import cv2
                import numpy as np

                clean_array = np.asarray(clean_crop)
                ink = cv2.threshold(
                    clean_array,
                    0,
                    255,
                    cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
                )[1]
                horizontal = cv2.morphologyEx(
                    ink,
                    cv2.MORPH_OPEN,
                    cv2.getStructuringElement(
                        cv2.MORPH_RECT,
                        (max(18, ink.shape[1] // 3), 2),
                    ),
                )
                line_clean = Image.fromarray(
                    cv2.bitwise_not(cv2.subtract(ink, horizontal))
                )
                line_clean.resize(
                    (
                        max(180, line_clean.width * 3),
                        max(96, line_clean.height * 3),
                    ),
                    Image.Resampling.LANCZOS,
                ).save(line_clean_path)
            except Exception:
                clean_crop.resize(
                    (
                        max(180, clean_crop.width * 3),
                        max(96, clean_crop.height * 3),
                    ),
                    Image.Resampling.LANCZOS,
                ).save(line_clean_path)
            _date_slot_white_canvas(clean_crop).save(white_path)
            _date_slot_white_canvas(crop_first_clean).save(crop_first_white_path)
            output[name] = {
                "original": raw_path,
                "processed": clean_path,
                "line_clean": line_clean_path,
                "white_processed": white_path,
                "crop_first_white_processed": crop_first_white_path,
            }
    return output


def _date_slot_white_canvas(image: Image.Image) -> Image.Image:
    """Standardize a small handwritten slot for OCR and visual review."""
    target_height = 180
    width = max(1, round(image.width * target_height / max(1, image.height)))
    normalized = image.resize((width, target_height), Image.Resampling.LANCZOS).convert(
        "RGB"
    )
    canvas = Image.new("RGB", (max(720, width + 160), 320), "white")
    canvas.paste(
        normalized,
        ((canvas.width - width) // 2, (canvas.height - target_height) // 2),
    )
    return canvas.resize(
        (canvas.width * 3, canvas.height * 3), Image.Resampling.LANCZOS
    )


def _save_date_upper_line_crop(
    source: str | Path,
    destination: str | Path,
) -> tuple[float, float, float, float]:
    """Save a right-aligned handwritten date placed in the row above."""
    left, top, right, bottom = 0.46, 0.0, 0.995, 0.58
    with Image.open(source) as image:
        width, height = image.size
        cropped = image.crop(
            (
                round(left * width),
                round(top * height),
                round(right * width),
                round(bottom * height),
            )
        )
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix.lower() in {".jpg", ".jpeg"}:
            cropped.convert("RGB").save(destination, quality=95)
        else:
            cropped.save(destination)
    return left, top, right - left, bottom - top
