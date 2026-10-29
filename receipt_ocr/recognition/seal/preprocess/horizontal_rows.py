"""Verify a split type row using observed neighbouring text, not a centre strip."""
import itertools
import math
from pathlib import Path

import numpy as np
from receipt_ocr.imaging.working_images import open_rgb

from .orientation_angles import _stamp_type_marker_strength
from .orientation_geometry import _rotation_geometry, _transform_points, rotate_stamp_image


def polygon_bounds(polygons, width, height, padding=0):
    points = np.asarray([p for polygon in polygons for p in polygon], dtype=float)
    if not len(points) or not np.isfinite(points).all():
        return None
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    pad = (bottom - top) * padding
    return [max(0, math.floor(left - pad)), max(0, math.floor(top - pad)),
            min(width, math.ceil(right + pad)), min(height, math.ceil(bottom + pad))]


def split_type_rows(boxes):
    """Adjacent, similarly sized detections may be fragments of one type row."""
    rows = []
    for box in boxes:
        points = np.asarray(box.get("points") or [], dtype=float)
        confidence = float(box.get("confidence") or 0)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or not math.isfinite(confidence) or confidence < .4):
            continue
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        if bottom <= top or right - left < .8 * (bottom - top):
            continue
        rows.append((box, (left, top, right, bottom)))
    for (a, ar), (b, br) in itertools.combinations(sorted(rows, key=lambda r: r[1][0]), 2):
        ah, bh = ar[3] - ar[1], br[3] - br[1]
        overlap, gap = min(ar[3], br[3]) - max(ar[1], br[1]), br[0] - ar[2]
        if not (overlap >= .5 * min(ah, bh) and max(ah, bh) <= 2 * min(ah, bh)
                and -.2 * min(ah, bh) <= gap <= max(ah, bh)):
            continue
        # Restrict grouping to observed type fragments, not company letters.
        text = str(a.get("text", "")) + str(b.get("text", ""))
        if not any(token in text for token in ("业务", "收货", "专用", "用章", "货专")):
            continue
        dx, dy = np.mean(b["points"], axis=0) - np.mean(a["points"], axis=0)
        angle = math.degrees(math.atan2(dy, dx))
        if abs(angle) <= 15:
            yield [a, b], angle


def grouped_type_candidate(source, boxes, scratch, band_dir, coarse, *, model_variant, read_row):
    """A group must read coherently before AND after its small correction."""
    from ..ocr.interface import detect_boxes

    with open_rgb(source) as image:
        width, height = image.size
    candidates = []
    for index, (group, angle) in enumerate(itertools.islice(split_type_rows(boxes), 6)):
        polygons = [box["points"] for box in group]
        focus = polygon_bounds(polygons, width, height, .08)
        band_path = Path(band_dir) / f"coarse-{coarse}-group-{index}.png"
        text, confidence = read_row(source, band_path, model_variant=model_variant, focus_box=focus)
        if _stamp_type_marker_strength(text) < 2 or len(text) < 3 or confidence < .8:
            continue
        fine = angle if abs(angle) > 2 else 0.0
        matrix, final_width, final_height = _rotation_geometry(width, height, fine)
        final_path = Path(source)
        if fine:
            final_path = Path(scratch) / f"coarse-{coarse}-group-{index}-fine.png"
            rotate_stamp_image(source, final_path, fine)
        final_polygons = [_transform_points(p, matrix) for p in polygons]
        focus = polygon_bounds(final_polygons, final_width, final_height, .08)
        final_text, final_confidence = read_row(
            final_path, band_path, model_variant=model_variant, focus_box=focus,
        )
        if final_text != text or final_confidence < .8:
            continue
        # Local detection is more precise than mapping the coarse fragments:
        # their tilted polygons can include the border and miss a middle glyph.
        local = detect_boxes(band_path, model_variant=model_variant)
        matches = [box for box in local if box.get("text") == text
                   and float(box.get("confidence") or 0) >= .8
                   and abs(float(box.get("angle") or 0)) <= 15
                   and len(box.get("points") or []) == 4]
        if matches:
            best = max(matches, key=lambda box: box["confidence"])
            final_polygons = [[[x + focus[0], y + focus[1]] for x, y in best["points"]]]
        exact = polygon_bounds(final_polygons, final_width, final_height)
        candidates.append({
            "angle": float(coarse), "path": final_path, "fine_rotation": fine,
            "fine_matrix": matrix, "candidate_width": width, "candidate_height": height,
            "final_width": final_width, "final_height": final_height,
            "focus_box": focus, "mask_polygons": final_polygons, "exact_box": exact,
            "anchor_text": "".join(box["text"] for box in group),
            "anchor_confidence": min(float(box["confidence"]) for box in group),
            "anchor_angle": angle, "anchor_points": [], "boxes": group, "decision": {},
            "band_text": final_text, "band_confidence": final_confidence, "error": "",
            "grouped_row": True, "local_mask_verified": bool(matches),
        })
    return max(candidates, key=lambda c: (len(c["band_text"]), c["band_confidence"]), default=None)
