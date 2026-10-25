"""Recover an oval border polluted by pale scan rules, without deleting ink."""
from pathlib import Path
from types import SimpleNamespace
import math

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import (
    _fit_unfiltered_ellipse_annulus as _fit_ellipse_annulus,
    _ellipse_normal_maps, _save_ellipse_annulus_unwrapped,
)


def _chroma(image, color):
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(image))
    return r - np.maximum(b, g) if color == "red" else b - np.maximum(r, g)


def _stable_fits(image, color):
    base = _fit_ellipse_annulus(_ocr_color_mask(image, color))
    strength = _chroma(image, color)
    colored = strength[strength > 5]
    if base is None or colored.size < 200:
        return None
    level = float(np.quantile(colored, .90))
    fits = [_fit_ellipse_annulus((strength > level * fraction).astype(np.uint8) * 255)
            for fraction in (.25, .5)]
    if not all(fits):
        return None
    first, second = fits
    scale = min(first[0][2][1])
    if not math.isfinite(scale) or scale <= 0:
        return None
    centers = np.linalg.norm(np.subtract(first[0][2][0], second[0][2][0]))
    axes = np.max(np.abs(np.subtract(first[0][2][1], second[0][2][1])))
    if not (centers < scale * .02 and axes < scale * .02
            and max(first[0][1], second[0][1]) < min(.025, base[0][1] * .5)):
        return None
    return fits, level, base[0][1]


def prepare_strong_border_ring(working, masked, destination, *, color, reader, provider):
    """Verify two normal-offset views after the ordinary border trace failed.

    Only the geometry masks use stronger ink. Both OCR images sample the
    original masked pixels, including faint glyphs. Neither expected text nor
    a requirement is an input. A failed verification leaves the old ring usable.
    """
    if color not in {"red", "blue"}:
        return None
    intact, image = cv2.imread(str(working)), cv2.imread(str(masked))
    if intact is None or image is None or image.shape != intact.shape:
        return None
    fitted = _stable_fits(intact, color)
    if fitted is None:
        return None
    fits, level, base_error = fitted
    # Preserve the same colour-safe grayscale used by the existing alternative.
    # In particular, do not apply the stronger geometry thresholds to OCR pixels.
    gray = np.rint(255 - np.clip(_chroma(image, color) * 255 / 160, 0, 255)).astype(np.uint8)
    mask = _ocr_color_mask(image, color)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    views = []
    for index, geometry in enumerate(fits):
        mx, my = _ellipse_normal_maps(*geometry)
        guide = SimpleNamespace(annulus=geometry, source_shape=image.shape[:2], map_x=mx, map_y=my)
        ring = destination.with_name(f"{destination.stem}-{index}.png")
        if not _save_ellipse_annulus_unwrapped(
                gray, mask, ring, single_line=True, proportional=True, ring_guide=guide):
            return None
        reads = reader.read_line(ring, provider=provider)
        text = "".join(row.text for row in reads).strip()
        scores = [float(row.confidence) for row in reads]
        if not text or not scores or any(not math.isfinite(score) or not .97 <= score <= 1 for score in scores):
            return None
        views.append({"text": text, "confidence": min(scores), "ring": ring, "guide": guide,
                      "threshold": level * (.25, .5)[index], "ellipse": geometry[0][2],
                      "fit_error": geometry[0][1]})
    if views[0]["text"] != views[1]["text"]:
        return None
    border = destination.with_name(f"{destination.stem}-border.png")
    overlay = intact.copy()
    cv2.ellipse(overlay, fits[0][0][2], (0, 130, 0), max(1, round(min(image.shape[:2]) / 450)), cv2.LINE_AA)
    if not cv2.imwrite(str(border), overlay):
        raise ValueError("深色笔画椭圆边框图保存失败")
    return {
        "ring": views[0]["ring"], "check_ring": views[1]["ring"],
        "guide": views[0]["guide"], "border": border,
        "evidence": {
            "method": "stable_strong_ellipse_normals", "status": "verified",
            "base_fit_error": base_error, "source_size": list(image.shape[1::-1]),
            "checks": [{key: view[key] for key in ("text", "confidence", "threshold", "ellipse", "fit_error")}
                       for view in views],
        },
    }
