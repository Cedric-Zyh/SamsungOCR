"""Receipt date region preparation."""
from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
from ..runtime.execution import timed
from .contracts import DateCropOutOfRange
from .io import _read_image, _write_stage_image


def _remove_date_red_stamp(crop: np.ndarray) -> np.ndarray:
    """Suppress red ink while preserving the observed dark stroke cores.

    Saturated red is bright in the red channel even when its luminance is
    low. Map that channel's 20..200 range to black..white, but retain the
    original grayscale wherever red is below 100 (including red-tinted
    black handwriting at stamp crossings). No strokes are reconstructed.
    """
    red = crop[:, :, 2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    cleaned = np.clip(
        (red.astype(np.float64) - 20) * 255 / 180, 0, 255
    ).astype(np.uint8)
    dark_core = red < 100
    cleaned[dark_core] = gray[dark_core]
    return cleaned


@timed('date_crop_generation')
def save_receipt_date_crop(
    source: str | Path,
    destination: str | Path,
    signature_anchor_y: float,
    *,
    tight: bool = True,
    raw_destination: str | Path | None = None,
    color_clean_destination: str | Path | None = None,
    left: float | None = None,
) -> tuple[float, float, float, float]:
    """Remove colored seal ink around the handwritten receiving date."""
    image = _read_image(source)
    height, width = image.shape[:2]
    if tight:
        x1n, x2n = 0.76, 0.995
        y1n = max(0.0, signature_anchor_y + 0.068)
        y2n = min(1.0, signature_anchor_y + 0.108)
    else:
        x1n, x2n = 0.72, 0.995
        y1n = max(0.0, signature_anchor_y + 0.052)
        y2n = min(1.0, signature_anchor_y + 0.112)
    if left is not None:
        x1n = max(0.0, min(float(left), x2n - 0.05))
    x1, x2 = int(x1n * width), int(x2n * width)
    y1, y2 = int(y1n * height), int(y2n * height)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise DateCropOutOfRange(
            f"日期裁剪区域超出页面范围：横向 {x1n:.3f}~{x2n:.3f}、"
            f"纵向 {y1n:.3f}~{y2n:.3f}（图片 {width}x{height}）"
        )
    if raw_destination:
        _write_stage_image(raw_destination, crop, ".jpg")
    # Both displayed date lines and OCR derivatives share the same cleanup.
    cleaned_gray = _remove_date_red_stamp(crop)
    if color_clean_destination:
        _write_stage_image(color_clean_destination, cleaned_gray, ".png")
    gray = cleaned_gray
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        ink,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, ink.shape[1] // 9), 2)),
    )
    # Date text has no vertical separators: long vertical ink belongs to
    # digits/characters and must also survive in this internal OCR input.
    ink = cv2.subtract(ink, horizontal)
    gray = cv2.bitwise_not(ink)
    target_width = 1500
    scale = target_width / max(1, gray.shape[1])
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", gray)
    if not ok:
        raise ValueError("日期裁剪图片编码失败")
    encoded.tofile(str(destination))
    return (x1n, y1n, x2n - x1n, y2n - y1n)
