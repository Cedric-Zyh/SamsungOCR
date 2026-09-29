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


from receipt_ocr.recognition.date.preprocess.slots import _save_date_slot_views


from receipt_ocr.recognition.date.preprocess.slots import _date_slot_white_canvas


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
