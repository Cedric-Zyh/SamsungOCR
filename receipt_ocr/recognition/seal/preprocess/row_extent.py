"""Measure clipped type rows from glyph baselines, without another detector."""
from pathlib import Path
import math

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border


def measure_row_extent(source, seed_box, *, color="red"):
    """Propose one bounded text envelope from several similarly sized glyphs.

    OCR boxes locate the search, not the final edge. Long thin remnants are
    excluded only from this measurement; recognition still uses original
    pixels. An uncertain baseline or unmeasurable border produces no proposal.
    """
    image = cv2.imread(str(source))
    box = np.asarray(seed_box, dtype=float)
    if image is None or box.shape != (4,) or not np.isfinite(box).all():
        return None
    left, top, right, bottom = box
    h = bottom - top
    if h < 16 or right - left < 3 * h:
        return None
    guide = trace_ellipse_border(source, color=color)
    if guide is None:
        return None
    center = np.asarray(guide.annulus[0][2][0])
    protected_interior = np.rint(center + .94 * (guide.points - center)).astype(np.int32)
    interior = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(interior, [protected_interior], 255)
    channels = image.astype(np.int16)
    ink = ((channels.max(2) - channels.min(2)) > 40).astype(np.uint8) * 255
    lines = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, np.ones((1, max(3, round(.55 * h))), np.uint8),
    )
    ink[lines > 0] = 0
    search = np.zeros_like(ink)
    search[max(0, round(top - .6 * h)):min(len(ink), round(bottom + .6 * h)),
           max(0, round(left - .8 * h)):min(ink.shape[1], round(right + .25 * h))] = 255
    ink &= search & interior
    closed = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE,
        np.ones((max(1, round(.115 * h)), max(1, round(.02 * h))), np.uint8),
    )
    _, _, stats, _ = cv2.connectedComponentsWithStats(closed)
    glyphs = np.asarray([
        (x + w / 2, y + height, height)
        for x, y, w, height, area in stats[1:]
        if .25 * h < w < .85 * h and .65 * h < height < 1.4 * h
        and area > .04 * h * h and x >= left and x + w <= right + .07 * h
    ], dtype=float)
    if len(glyphs) < 4 or np.ptp(glyphs[:, 0]) < .5 * (right - left):
        return None
    slopes = [
        (b[1] - a[1]) / (b[0] - a[0])
        for i, a in enumerate(glyphs) for b in glyphs[i + 1:]
        if abs(b[0] - a[0]) > h
    ]
    if not slopes:
        return None
    slope = float(np.median(slopes))
    intercept = float(np.median(glyphs[:, 1] - slope * glyphs[:, 0]))
    inliers = np.abs(glyphs[:, 1] - slope * glyphs[:, 0] - intercept) <= .12 * h
    glyphs = glyphs[inliers]
    if (len(glyphs) < 4 or np.ptp(glyphs[:, 0]) < .5 * (right - left)
            or abs(slope) > math.tan(math.radians(20))):
        return None
    height = float(np.median(glyphs[:, 2]))
    if np.median(np.abs(glyphs[:, 2] - height)) > .15 * height:
        return None
    x0 = max(0, round(left - .65 * height))
    x1 = min(image.shape[1], round(right + .2 * height))
    yy, xx = np.indices(ink.shape)
    band = ((xx >= x0) & (xx < x1)
            & (yy >= slope * xx + intercept - 1.04 * height)
            & (yy <= slope * xx + intercept + .05 * height))
    counts = ((ink > 0) & band).sum(axis=0)
    active = np.flatnonzero(counts > .07 * height)
    if not len(active):
        return None
    margin = max(2, round(.025 * height))
    x0 = max(x0, int(active.min()) - margin)
    x1 = min(x1, int(active.max()) + margin + 1)
    polygon = np.asarray([
        [x0, slope * x0 + intercept - 1.04 * height],
        [x1, slope * x1 + intercept - 1.04 * height],
        [x1, slope * x1 + intercept + .05 * height],
        [x0, slope * x0 + intercept + .05 * height],
    ])
    # Only intervene when the observed strokes expose a materially clipped row.
    if (x0 >= left - .2 * h and polygon[:, 1].min() >= top - .2 * h
            and polygon[:, 1].max() <= bottom + .2 * h):
        return None
    if polygon[:, 1].min() < 0 or polygon[:, 1].max() >= len(image):
        return None
    return {
        "method": "glyph_baseline", "polygon": polygon.round(2).tolist(),
        "protected_interior": protected_interior.tolist(),
        "glyph_count": len(glyphs), "baseline_angle": math.degrees(math.atan(slope)),
        "glyph_height": height, "seed_box": box.tolist(),
    }


def save_row_polygon(source, destination, polygon):
    """Save the same polygon used for masking as a single rectified OCR line."""
    image = cv2.imread(str(source))
    points = np.asarray(polygon, np.float32)
    width = round(float(np.linalg.norm(points[1] - points[0])))
    height = round(float((np.linalg.norm(points[3] - points[0])
                          + np.linalg.norm(points[2] - points[1])) / 2))
    target = np.asarray([[0, 0], [width - 1, 0], [width - 1, height - 1],
                         [0, height - 1]], np.float32)
    matrix = cv2.getPerspectiveTransform(points, target)
    row = cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                              borderValue=(255, 255, 255))
    row = cv2.copyMakeBorder(row, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), row):
        raise ValueError("完整横排文字裁图保存失败")


def verify_row_extent(source, destination, seed_box, previous_text, previous_confidence,
                      *, reader, provider, color="red"):
    """Verify a complete row before choosing the final whole-stamp rotation."""
    if len(previous_text) < 3:
        return None
    proposal = measure_row_extent(source, seed_box, color=color)
    if proposal is None:
        return None
    save_row_polygon(source, destination, proposal["polygon"])
    rows = reader.read_line(destination, provider=provider)
    if len(rows) != 1:
        return None
    text, confidence = rows[0].text, float(rows[0].confidence or 0)
    if (not math.isfinite(confidence) or confidence < max(.9, previous_confidence - .03)
            or not len(previous_text) < len(text) <= len(previous_text) + 2
            or not text.endswith(previous_text[-min(4, len(previous_text)):])):
        return None
    proposal.update(text=text, confidence=confidence)
    return proposal
