"""Verify a compact type-row polygon in the final stamp coordinate frame."""
import math
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border
from .orientation_angles import _stamp_type_marker_strength
from .orientation_geometry import _rotation_geometry
from .row_extent import save_row_polygon


def _glyph_envelope(image, polygon, seed_height, guide, character_count):
    """Measure similarly sized glyph bodies; morphology never reaches OCR."""
    height, width = image.shape[:2]
    center = np.asarray(guide.annulus[0][2][0])
    interior = np.zeros((height, width), np.uint8)
    cv2.fillPoly(interior, [np.rint(center + .92 * (guide.points - center)).astype(np.int32)], 1)
    ink = ((image.max(2).astype(np.int16) - image.min(2)) > 40).astype(np.uint8)
    kernel = max(1, round(seed_height * .012))
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((kernel, kernel), np.uint8)) * interior
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    h = seed_height
    ids = [i for i, (x, y, w, gh, area) in enumerate(stats[1:], 1)
           if .15 * h < w < .45 * h and .3 * h < gh < .65 * h
           and area > .035 * h * h
           and cv2.pointPolygonTest(polygon.astype(np.float32), (x + w / 2, y + gh / 2), False) >= 0]
    # An incomplete or merged set cannot establish the full line's extent.
    if len(ids) < 4 or len(ids) != character_count:
        return None
    bodies = stats[ids]
    mid = bodies[:, :2] + bodies[:, 2:4] / 2
    slopes = [(q[1] - p[1]) / (q[0] - p[0]) for i, p in enumerate(mid) for q in mid[i + 1:]
              if abs(q[0] - p[0]) > .25 * h]
    if not slopes:
        return None
    slope = float(np.median(slopes))
    glyph_height = float(np.median(bodies[:, 3]))
    residual = mid[:, 1] - slope * mid[:, 0]
    if (abs(slope) > math.tan(math.radians(20))
            or np.max(np.abs(residual - np.median(residual))) > .2 * glyph_height):
        return None
    angle = math.degrees(math.atan(slope))
    matrix, _, _ = _rotation_geometry(width, height, angle)
    y, x = np.nonzero(np.isin(labels, ids))
    points = cv2.transform(np.column_stack((x, y)).astype(np.float32)[None], matrix)[0]
    margin = max(3, round(glyph_height * .05))
    left, top = np.floor(points.min(0) - margin)
    right, bottom = np.ceil(points.max(0) + margin) + 1
    corners = np.array([[[left, top], [right, top], [right, bottom], [left, bottom]]], np.float32)
    tight = cv2.transform(corners, cv2.invertAffineTransform(matrix))[0]
    if cv2.contourArea(tight) >= .9 * cv2.contourArea(polygon.astype(np.float32)):
        return None
    return tight, len(ids), angle


def prepare_local_type_row(source, destination, orientation, *, reader, provider, color="red"):
    """Repair suspiciously tall/clipped row boxes without changing rotation.

    A bounded search supplies context to one detector call. Its verified
    polygon, optionally tightened from glyphs, is shared by OCR and masking.
    The expected customer/requirement string is never an input.
    """
    if orientation.get("horizontal_row_localization") or orientation.get("partial_anchor"):
        return None
    seed = orientation.get("oriented_type_row_exact_box") or orientation.get("type_row_exact_box")
    search = orientation.get("oriented_type_row_box") or orientation.get("type_row_box")
    previous = str(orientation.get("type_band_text") or orientation.get("anchor_text") or "")
    if not seed or not search or len(previous) < 3 or _stamp_type_marker_strength(previous) < 2:
        return None
    seed = np.asarray(seed, float)
    search = np.asarray(search, float)
    if seed.shape != (4,) or search.shape != (4,) or not np.isfinite([seed, search]).all():
        return None
    seed_height = seed[3] - seed[1]
    if seed_height < 16 or seed[2] <= seed[0] or seed[2] - seed[0] >= 3 * seed_height:
        return None
    image = cv2.imread(str(source))
    if image is None:
        return None
    height, width = image.shape[:2]
    left, top, right, bottom = map(int, np.rint(search))
    left = max(0, left - round(.3 * (bottom - top)))
    top, right, bottom = max(0, top), min(width, right), min(height, bottom)
    if right - left < 16 or bottom - top < 16:
        return None
    destination = Path(destination)
    search_path = destination.with_name(destination.stem + "-search.png")
    search_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(search_path), image[top:bottom, left:right]):
        raise ValueError("横排文字定位图保存失败")
    candidates = []
    for box in reader.detect_boxes(search_path, provider=provider):
        text = str(box.get("text") or "")
        confidence = float(box.get("confidence") or 0)
        points = np.asarray(box.get("points") or [], float)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or not math.isfinite(confidence) or confidence < .9
                or _stamp_type_marker_strength(text) < 2
                or not len(previous) <= len(text) <= len(previous) + 2
                or not text.endswith(previous[-min(4, len(previous)):])):
            continue
        # A touching edge may hide another leading glyph; do not certify it.
        if (np.any(points.min(0) < 2)
                or np.any(points.max(0) > [right - left - 3, bottom - top - 3])):
            continue
        polygon = points + [left, top]
        lo, hi = polygon.min(0), polygon.max(0)
        overlap = np.maximum(0, np.minimum(hi, seed[2:]) - np.maximum(lo, seed[:2])).prod()
        if overlap < .4 * min(np.prod(hi - lo), np.prod(seed[2:] - seed[:2])):
            continue
        candidates.append((text, confidence, polygon))
    if not candidates:
        return None
    text, _, polygon = max(candidates, key=lambda item: (len(item[0]), item[1]))
    guide = trace_ellipse_border(source, color=color)
    measured = _glyph_envelope(image, polygon, seed_height, guide, len(text)) if guide else None
    glyph_count, angle = None, None
    if measured:
        polygon, glyph_count, angle = measured
    if np.any(polygon.min(0) < 0) or np.any(polygon.max(0) >= [width, height]):
        return None
    coverage = np.zeros((height, width), np.uint8)
    cv2.fillPoly(coverage, [np.rint(polygon).astype(np.int32)], 255)
    border = np.zeros_like(coverage)
    if guide is not None:
        cv2.polylines(border, [np.rint(guide.points).astype(np.int32)], True,
                      255, max(5, round(min(height, width) * .02)))
        # A broad detected box near the rim must first be measured from glyphs.
        if not measured and np.any((coverage > 0) & (border > 0)):
            return None
    save_row_polygon(source, destination, polygon)
    rows = reader.read_line(destination, provider=provider)
    if len(rows) != 1 or rows[0].text != text:
        return None
    confidence = float(rows[0].confidence or 0)
    if not math.isfinite(confidence) or confidence < .9:
        return None
    return {
        "method": "local_glyph_envelope" if measured else "local_detection",
        "polygon": polygon.tolist(), "search_box": [left, top, right, bottom],
        "seed_box": seed.tolist(), "text": text, "confidence": confidence,
        "glyph_count": glyph_count, "baseline_angle": angle,
        "coordinate_frame": "final_oriented_stamp", "border_protected": guide is not None,
        "coverage": (coverage > 0) & (border == 0),
    }
