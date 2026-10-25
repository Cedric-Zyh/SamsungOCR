"""Separate thin bridges for row localization without filtering OCR pixels."""
import math
import re

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border
from .orientation_angles import _stamp_type_marker_strength


def separate_type_row_bridges(source, image, polygon_mask, selected, *,
                              text, confidence, color="red"):
    """Recover joined glyphs only for a verified, fully measured type row.

    Opening is confined to a localization copy. The output whites original
    pixels near its glyph cores, inside the observed polygon and measured rim.
    Neither required seal text nor another OCR pass participates in this step.
    """
    text = re.sub(r"\s+", "", str(text or ""))
    if (not math.isfinite(confidence) or confidence < .9
            or not re.fullmatch(r"[\u4e00-\u9fff]{4,16}", text)
            or _stamp_type_marker_strength(text) < 2):
        return None
    y, x = np.nonzero(polygon_mask)
    if not len(y):
        return None
    row_height = int(y.max() - y.min() + 1)
    if row_height < 16:
        return None
    kernel = max(3, round(row_height * .025))
    kernel += kernel % 2 == 0
    chroma = image.max(2).astype(np.int16) - image.min(2)
    core = cv2.morphologyEx((chroma >= 16).astype(np.uint8), cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(core, 8)
    inside = np.bincount(labels[polygon_mask > 0], minlength=count)
    bodies = stats[:, cv2.CC_STAT_AREA]
    ids = np.flatnonzero((inside >= .98 * bodies) & (bodies > .04 * row_height ** 2))
    ids = ids[ids != 0]
    # Merged, missing or extra bodies cannot establish a complete row.
    if len(ids) != len(text):
        return None
    bounds = stats[ids]
    if (np.any(bounds[:, 2] < .15 * row_height)
            or np.any(bounds[:, 2] > 1.2 * row_height)
            or np.any(bounds[:, 3] < .35 * row_height)
            or np.any(bounds[:, 3] > 1.2 * row_height)):
        return None
    mid = centers[ids]
    slopes = [(q[1] - p[1]) / (q[0] - p[0])
              for i, p in enumerate(mid) for q in mid[i + 1:]
              if abs(q[0] - p[0]) > .25 * row_height]
    if not slopes:
        return None
    slope = float(np.median(slopes))
    residual = mid[:, 1] - slope * mid[:, 0]
    if (abs(slope) > math.tan(math.radians(20))
            or np.ptp(residual) > .25 * np.median(bounds[:, 3])):
        return None
    glyph_core = np.isin(labels, ids)
    missing = glyph_core & ~selected
    if np.count_nonzero(missing) < max(4, .02 * np.count_nonzero(glyph_core)):
        return None

    guide = trace_ellipse_border(source, color=color)
    if guide is None:
        return None
    center = np.asarray(guide.annulus[0][2][0])
    interior = np.zeros_like(polygon_mask)
    cv2.fillPoly(interior, [np.rint(center + .94 * (guide.points - center)).astype(np.int32)], 255)
    protected = np.zeros_like(polygon_mask)
    cv2.polylines(protected, [np.rint(guide.points).astype(np.int32)], True,
                  255, max(5, round(min(image.shape[:2]) * .02)))
    safe = (polygon_mask > 0) & (interior > 0) & (protected == 0)

    distance, nearest = cv2.distanceTransformWithLabels(
        1 - core, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
    # Map distance-transform component labels rather than assuming they share
    # the numbering of connectedComponentsWithStats.
    belongs = np.zeros(int(nearest.max()) + 1, bool)
    belongs[np.unique(nearest[glyph_core])] = True
    belongs[0] = False
    nearby = belongs[nearest] & (distance <= max(6, 2 * kernel))
    ink = np.any(image < 255, axis=2)
    recovered = (selected | nearby) & safe & ink
    added = int(np.count_nonzero(recovered & ~selected))
    if not added:
        return None
    return recovered, {
        "method": "verified_thin_bridge_glyphs", "text": text,
        "confidence": float(confidence), "glyph_count": len(ids),
        "localization_kernel": kernel, "additional_ink_pixels": added,
        "protected_border": guide.summary(),
        "outside_polygon_changed": 0, "protected_border_changed": 0,
    }
