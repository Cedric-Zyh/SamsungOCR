"""Recover verified horizontal ink still joined to neighbouring artwork."""
import math
import re
from types import SimpleNamespace

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus, _ellipse_normal_maps
from receipt_ocr.imaging.ring_border import trace_ellipse_border
from .orientation_angles import _stamp_type_marker_strength
from .orientation_geometry import _rotation_geometry


def _row_geometry(source, orientation, color, image_shape):
    """Reuse the intact pre-enhancement geometry, before any white mask."""
    image = cv2.imread(str(source))
    if image is None:
        return None
    for angle in orientation.get("rotation_steps", []):
        height, width = image.shape[:2]
        matrix, width, height = _rotation_geometry(width, height, angle)
        image = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                               borderValue=(255, 255, 255))
    if image.shape[:2] != image_shape:
        return None
    annulus = _fit_ellipse_annulus(_ocr_color_mask(image, color))
    if annulus is None:
        return None
    map_x, map_y = _ellipse_normal_maps(*annulus)
    return SimpleNamespace(annulus=annulus, source_shape=image_shape,
                           map_x=map_x, map_y=map_y)


def _row_anchors(core, polygon, safe, height, width):
    # Independent glyphs cannot borrow the star's lower tip through a bridge.
    count, labels, bounds, _ = cv2.connectedComponentsWithStats(core, 8)
    inside = np.bincount(labels[polygon > 0], minlength=count)
    ids = np.flatnonzero(
        (inside >= .98 * bounds[:, 4]) & (bounds[:, 4] > .04 * height ** 2)
        & (bounds[:, 2] >= .15 * height) & (bounds[:, 2] <= 1.2 * height)
        & (bounds[:, 3] >= .35 * height) & (bounds[:, 3] <= 1.2 * height))
    ids = ids[ids != 0]
    if len(ids) >= 3:
        return bounds[ids], "independent_observed_glyphs"

    # A whole verified row can remain merged. Do not require one component
    # per OCR character, but reject bodies cut by the detector's boundary.
    clipped = core.copy()
    clipped[~safe | (polygon == 0)] = 0
    count, labels, bounds, _ = cv2.connectedComponentsWithStats(clipped, 8)
    eroded = cv2.erode(polygon, np.ones((5, 5), np.uint8))
    boundary = np.bincount(labels[(polygon > 0) & (eroded == 0)], minlength=count)
    ids = np.flatnonzero(
        (bounds[:, 4] > .025 * height ** 2)
        & (bounds[:, 2] >= .15 * height) & (bounds[:, 2] <= .98 * width)
        & (bounds[:, 3] >= .35 * height) & (bounds[:, 3] <= 1.2 * height)
        & (boundary < .01 * bounds[:, 4]))
    return bounds[ids[ids != 0]], "merged_observed_row"


def recover_residual_type_row(source, destination, mask_path, *, polygons,
                              orientation, geometry_source, ring_guide=None,
                              color="red"):
    """Persist measured row ink only when the existing mask leaves real glyphs.

    Uses actual verified OCR and image geometry; no required text, extra OCR,
    or character correction. Uncertain/already-clean inputs keep their files.
    """
    text = re.sub(r"\s+", "", str(orientation.get("type_band_text") or ""))
    confidence = float(orientation.get("type_band_confidence") or 0)
    read_box = orientation.get("oriented_type_row_read_box")
    if (orientation.get("partial_anchor") or not math.isfinite(confidence)
            or confidence < .9 or not re.fullmatch(r"[\u4e00-\u9fff]{4,16}", text)
            or _stamp_type_marker_strength(text) < 2 or not polygons
            or read_box is None or len(read_box) != 4):
        return None
    image, previous = cv2.imread(str(source)), cv2.imread(str(destination))
    if image is None or previous is None or image.shape != previous.shape:
        return None
    polygon = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(polygon, [np.rint(p).astype(np.int32) for p in polygons], 255)
    ys, xs = np.nonzero(polygon)
    if not len(ys):
        return None
    height, width = int(np.ptp(ys) + 1), int(np.ptp(xs) + 1)
    if height < 16:
        return None
    kernel = max(3, round(height * .025))
    kernel += kernel % 2 == 0
    opening = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    previous_chroma = previous.max(2).astype(np.int16) - previous.min(2)
    remaining = ((previous_chroma >= 16) & (polygon > 0)).astype(np.uint8)
    minimum = max(16, round(.01 * height ** 2))
    if np.count_nonzero(cv2.morphologyEx(remaining, cv2.MORPH_OPEN, opening)) < minimum:
        return None
    guide = ring_guide or _row_geometry(geometry_source, orientation, color, image.shape[:2])
    if guide is None or guide.source_shape != image.shape[:2]:
        return None
    measured = trace_ellipse_border(source, color=color)
    if measured is None:
        return None
    protected = np.zeros_like(polygon)
    cv2.polylines(protected, [np.rint(measured.points).astype(np.int32)], True,
                  255, max(5, round(min(image.shape[:2]) * .05)))
    center, axes, angle = guide.annulus[0][2]
    interior = np.zeros_like(polygon)
    cv2.ellipse(interior, (center, (axes[0] * .94, axes[1] * .94), angle), 255, -1)
    safe = (interior > 0) & (protected == 0)
    chroma = image.max(2).astype(np.int16) - image.min(2)
    core = cv2.morphologyEx((chroma >= 16).astype(np.uint8), cv2.MORPH_OPEN, opening)
    anchors, mode = _row_anchors(core, polygon, safe, height, width)
    if not len(anchors):
        return None
    span = int(np.max(anchors[:, 0] + anchors[:, 2]) - np.min(anchors[:, 0]))
    if not ((len(anchors) >= 3 and span > .3 * width)
            or np.max(anchors[:, 2]) > .55 * width):
        return None
    margin = max(2, round(float(np.median(anchors[:, 3])) * .02))
    x0, y0, x1, y1 = map(lambda value: int(round(value)), read_box)
    top = max(0, y0, int(anchors[:, 1].min()) - margin)
    bottom = min(image.shape[0], y1 + 1,
                 int(np.max(anchors[:, 1] + anchors[:, 3])) + margin)
    if bottom <= top or x1 <= x0:
        return None
    region = np.zeros_like(polygon)
    region[top:bottom, max(0, x0):min(image.shape[1], x1 + 1)] = 1
    bounded = core.copy()
    bounded[~safe | (region == 0)] = 0
    count, labels, bounds, _ = cv2.connectedComponentsWithStats(bounded, 8)
    inside = np.bincount(labels[polygon > 0], minlength=count)
    ids = np.flatnonzero(
        (inside >= .5 * bounds[:, 4]) & (bounds[:, 4] >= max(8, .0015 * height ** 2))
        & (bounds[:, 2] <= 20 * np.maximum(1, bounds[:, 3])))
    ids = ids[ids != 0]
    if not len(ids):
        return None
    distance = cv2.distanceTransform((~np.isin(labels, ids)).astype(np.uint8), cv2.DIST_L2, 5)
    selected = (safe & (region > 0) & (distance <= max(6, 2 * kernel))
                & np.any(image < 255, axis=2))
    residual = ((previous_chroma >= 16) & selected).astype(np.uint8)
    missing = int(np.count_nonzero(cv2.morphologyEx(residual, cv2.MORPH_OPEN, opening)))
    if missing < minimum:
        return None
    output = image.copy()
    output[selected] = 255
    if not cv2.imwrite(str(mask_path), selected.astype(np.uint8) * 255):
        raise ValueError("横排残留文字填白范围保存失败")
    if not cv2.imwrite(str(destination), output):
        raise ValueError("横排残留文字填白图片保存失败")
    return guide, {
        "method": "verified_residual_row_glyphs", "text": text,
        "confidence": confidence, "anchor_mode": mode,
        "anchor_boxes": anchors.tolist(), "row_envelope": [top, bottom],
        "remaining_glyph_core_before": missing, "minimum_residue": minimum,
        "localization_kernel": kernel, "mask_pixels": int(selected.sum()),
        "protected_border": measured.summary(),
        "protected_border_changed": 0, "outside_vertical_envelope_changed": 0,
    }
