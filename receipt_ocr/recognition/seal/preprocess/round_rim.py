"""Save one calibrated round-ring input and its coordinate-identical evidence."""
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _save_ellipse_annulus_unwrapped
from receipt_ocr.imaging.round_rim import _chroma, calibrate_round_ring
from receipt_ocr.imaging.ring_border import save_ring_border_overlay
from receipt_ocr.imaging.unwrap import save_ring_text_band_preview


def prepare_round_rim(working, masked, destination, *, color):
    intact, image = cv2.imread(str(working)), cv2.imread(str(masked))
    guide, evidence = calibrate_round_ring(intact, image, color=color)
    if guide is None:
        return None, evidence
    ring = Path(destination)
    band = ring.with_name(f"{ring.stem}-band.png")
    border = ring.with_name(f"{ring.stem}-border.png")
    gray = (255-np.clip(_chroma(image, color)*255/160, 0, 255)).astype(np.uint8)
    if not _save_ellipse_annulus_unwrapped(
            gray, _ocr_color_mask(image, color), ring, single_line=True, ring_guide=guide):
        return None, {**evidence, "status": "fallback", "reason": "unwrap_failed"}
    save_ring_text_band_preview(masked, band, color=color, ring_guide=guide)
    save_ring_border_overlay(working, border, guide)
    overlay = cv2.imread(str(border))
    inner = np.rint(np.column_stack((guide.map_x[-1], guide.map_y[-1]))).astype(np.int32)
    cv2.polylines(overlay, [inner], True, (220, 100, 0),
                  max(1, round(min(image.shape[:2])/450)), cv2.LINE_AA)
    if not cv2.imwrite(str(border), overlay):
        raise ValueError("圆章取样边界核对图保存失败")
    return {"ring": ring, "band": band, "border": border, "guide": guide}, evidence
