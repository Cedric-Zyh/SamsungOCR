"""Region crop and seal ink preparation."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from .contracts import SealRegion
from .io import _read_image
from .colors import _color_masks, _ocr_color_mask


def save_isolated_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Save colored ink from one stamp as high-contrast black-on-white OCR input."""
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    red, blue = _color_masks(crop)
    mask = red if region.color == "red" else blue
    mask = cv2.dilate(mask, np.ones((2, 2), np.uint8), iterations=1)
    canvas = np.full(crop.shape[:2], 255, dtype=np.uint8)
    canvas[mask > 0] = 0
    target_width = max(1000, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("印章裁剪图片编码失败")
    encoded.tofile(str(destination))


def save_color_isolated_seal(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Keep only colored stamp ink on white while preserving stroke intensity.

    Binary isolation is useful for faint contours, but it removes the local
    intensity variation that OCR detectors sometimes need to separate curved
    Chinese glyphs.  This variant retains the original red/blue pixels and
    removes every neutral printed form pixel, so it is safe matching evidence
    even when the stamp overlaps the printed signature requirement.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    mask = _ocr_color_mask(crop, region.color)
    canvas = np.full_like(crop, 255)
    canvas[mask > 0] = crop[mask > 0]
    target_width = max(1000, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("保留章色裁剪图片编码失败")
    encoded.tofile(str(destination))


def save_rectangular_seal_code_line(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    """Save the lower numeric band of a rectangular station/code stamp.

    Heavy rectangular service stamps often contain small organization text in
    the upper half and one large 6--12 digit identifier in the lower half.
    Whole-stamp OCR can merge the border and upper text into the first digits.
    This derivative keeps only colored ink, removes long rectangle borders,
    and exposes the lower band as black-on-white OCR evidence.  It never uses
    neutral printed form text and is therefore safe for stamp matching.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("矩形编号章裁剪为空")
    crop_height, crop_width = crop.shape[:2]
    # Stay inside the rectangular border instead of trying to remove it with
    # long-line morphology.  The latter also erases adjoining wide digit
    # strokes on heavily inked real stamps.  Reviewed station stamps place the
    # large identifier in this lower inner band.
    band_y1 = max(0, int(crop_height * 0.44))
    band_y2 = min(crop_height, int(crop_height * 0.86))
    band_x1 = max(0, int(crop_width * 0.10))
    band_x2 = min(crop_width, int(crop_width * 0.92))
    band = crop[band_y1:band_y2, band_x1:band_x2]
    b, g, r = (channel.astype(np.int16) for channel in cv2.split(band))
    if region.color == "red":
        dominance = r - np.maximum(g, b)
    else:
        dominance = b - np.maximum(g, r)
    dominance = np.clip(dominance, 0, 255).astype(np.uint8)
    # A heavy stamp can leave a pale red/blue wash across the whole box.  The
    # large identifier remains much more channel-dominant than that wash.
    # Preserve the continuous dominance instead of hard-thresholding: the
    # recognition-only Paddle model needs local intensity variation to keep
    # adjacent digits such as 6/8 distinct.
    dominance = cv2.normalize(dominance, None, 0, 255, cv2.NORM_MINMAX)
    canvas = 255 - dominance
    target_width = max(1400, canvas.shape[1])
    scale = target_width / max(1, canvas.shape[1])
    canvas = cv2.resize(
        canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
    )
    canvas = cv2.copyMakeBorder(
        canvas, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise ValueError("矩形编号章数字行编码失败")
    encoded.tofile(str(destination))


def save_region_crop(
    source: str | Path,
    destination: str | Path,
    region: SealRegion,
) -> None:
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1 = int(region.x * width), int(region.y * height)
    x2 = int((region.x + region.width) * width)
    y2 = int((region.y + region.height) * height)
    crop = image[y1:y2, x1:x2]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise ValueError("印章原始区域编码失败")
    encoded.tofile(str(destination))


def save_pixel_region_crop(
    source: str | Path,
    destination: str | Path,
    box: tuple[float, float, float, float],
) -> None:
    """Write the absolute pixel box ``(x1, y1, x2, y2)`` of the source image.

    :class:`SealRegion` carries page fractions because that is the currency of
    our own detection.  A box returned by an external service already *is*
    geometry, so it is written directly instead of being round-tripped through
    fractions first, which would move the corners by up to a pixel each.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (int(round(float(value))) for value in box)
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError("印章区域超出图片范围")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise ValueError("印章区域编码失败")
    encoded.tofile(str(destination))
