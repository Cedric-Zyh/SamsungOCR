"""Seal text band crops."""
from __future__ import annotations

from pathlib import Path
import cv2
from .io import _read_image


def save_unwrapped_seal_bands(
    source: str | Path,
    destination_prefix: str | Path,
) -> list[Path]:
    """Save the three independent polar strips used by round-seal OCR.

    ``save_unwrapped_seal`` deliberately keeps three angular shifts in one
    auditable image.  On very shallow seals, feeding that tall contact sheet
    to OCR can shrink each company line too aggressively.  This helper only
    accepts the exact current 3-strip/18-pixel-gap layout and never guesses at
    legacy artifacts.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    strip_height, remainder = divmod(height - 36, 3)
    if remainder or strip_height <= 0:
        return []
    prefix = Path(destination_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output = []
    for index in range(3):
        top = index * (strip_height + 18)
        band = image[top : top + strip_height, :width]
        destination = prefix.with_name(f"{prefix.stem}-{index + 1}.png")
        ok, encoded = cv2.imencode(".png", band)
        if not ok:
            raise ValueError("圆章展开分带图片编码失败")
        encoded.tofile(str(destination))
        output.append(destination)
    return output


def save_rectangular_seal_bands(
    source: str | Path,
    destination_prefix: str | Path,
) -> list[Path]:
    """Split a color-safe normalized rectangular stamp into three OCR rows.

    Each third is inset by about 0.8% at internal boundaries.  The narrow white
    separation keeps the company row from being detected together with the
    centre/bottom stamp type, which caused ``电子`` to regress to ``电于`` in
    a reviewed rectangular seal.  The complete normalized image remains an
    independent visible artifact, so these focused OCR rows are additive.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 30 or width < 30:
        return []
    inset = max(2, round(height * 0.008))
    prefix = Path(destination_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output = []
    for index in range(3):
        top = round(index * height / 3) + (inset if index else 0)
        bottom = min(
            height,
            round((index + 1) * height / 3)
            - (inset if index < 2 else 0),
        )
        band = image[top:bottom, :width]
        destination = prefix.with_name(f"{prefix.stem}-{index + 1}.png")
        ok, encoded = cv2.imencode(".png", band)
        if not ok:
            raise ValueError("矩形印章横向分带图片编码失败")
        encoded.tofile(str(destination))
        output.append(destination)
    return output


def save_round_seal_type_band(
    source: str | Path,
    destination: str | Path,
    *,
    orientation_aligned: bool = False,
    focus_box: list[float] | tuple[float, float, float, float] | None = None,
) -> Path:
    """Save the inner type row of a color-isolated round stamp.

    Reviewed circular customer stamps commonly put the legal company around
    the rim and a horizontal type such as ``手机售后专用章`` inside it.
    ``orientation_aligned`` is used after the stamp-type polygon has rotated
    the crop upright; that layout puts the row around the centre of the
    expanded canvas.  The legacy lower-inner slice remains the default for
    unrotated crops.  ``source`` must already be a color-only white-background
    derivative, so the focused band cannot import the black printed signature
    requirement into seal-matching evidence.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 40 or width < 40:
        raise ValueError("圆章章类型分带图片过小")
    left, top, right, bottom = round_seal_type_band_box(
        source, orientation_aligned=orientation_aligned, focus_box=focus_box
    )
    band = image[top:bottom, left:right]
    if band.size == 0:
        raise ValueError("圆章章类型分带为空")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", band)
    if not ok:
        raise ValueError("圆章章类型分带图片编码失败")
    encoded.tofile(str(destination))
    return destination


def round_seal_type_band_box(
    source: str | Path,
    *,
    orientation_aligned: bool = False,
    focus_box: list[float] | tuple[float, float, float, float] | None = None,
) -> tuple[int, int, int, int]:
    """Return the pixel box used by :func:`save_round_seal_type_band`."""
    image = _read_image(source)
    height, width = image.shape[:2]
    if height < 40 or width < 40:
        raise ValueError("圆章章类型分带图片过小")
    if focus_box and len(focus_box) == 4:
        left = max(0, round(float(focus_box[0])))
        top = max(0, round(float(focus_box[1])))
        right = min(width, round(float(focus_box[2])))
        bottom = min(height, round(float(focus_box[3])))
    else:
        left, right = round(width * 0.08), round(width * 0.92)
        if orientation_aligned:
            top, bottom = round(height * 0.42), round(height * 0.62)
        else:
            top, bottom = round(height * 0.56), round(height * 0.84)
    return left, top, right, bottom
