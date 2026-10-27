"""Read an independent Latin brand row before masking interior stamp text."""
import math
import re
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ring_border import trace_ellipse_border


def prepare_horizontal_brand_rows(source, directory, index, orientation, *, reader, provider, color="red"):
    """Keep verified brand/type rows separate from the curved company name.

    Both detection and crop recognition must agree. The observed type row is
    the anchor, not a required company/brand string. Every selected polygon
    must sit wholly inside the measured border, away from the company arc.
    Return a complete plan before the caller replaces any existing evidence.
    """
    anchor = re.sub(r"\s+", "", orientation.get("type_band_text") or "")
    focus = orientation.get("oriented_type_row_exact_box") or orientation.get("type_row_exact_box")
    if not anchor or not focus or orientation.get("partial_anchor"):
        return None
    image = cv2.imread(str(source))
    if image is None:
        return None
    height, width = image.shape[:2]
    accepted = []
    for box in reader.detect_boxes(source, provider=provider):
        text = re.sub(r"\s+", "", box.get("text") or "")
        confidence = float(box.get("confidence") or 0)
        points = np.asarray(box.get("points") or [], dtype=float)
        if (not math.isfinite(confidence) or confidence < .95
                or points.shape != (4, 2) or not np.isfinite(points).all()):
            continue
        dx, dy = points[1] - points[0]
        row_width, row_height = np.linalg.norm(points[1]-points[0]), np.linalg.norm(points[3]-points[0])
        if row_width < 2 * row_height or abs(math.degrees(math.atan2(dy, dx))) > 12:
            continue
        left, top = points.min(0)
        right, bottom = points.max(0)
        if left < 0 or top < 0 or right >= width or bottom >= height:
            continue
        kind = ""
        if text == anchor:
            overlap = max(0, min(right, focus[2])-max(left, focus[0])) * max(0, min(bottom, focus[3])-max(top, focus[1]))
            if overlap >= .6 * (right-left) * (bottom-top):
                kind = "type"
        elif (re.fullmatch(r"[A-Za-z][A-Za-z0-9.&-]{2,23}", text)
              and len(re.findall(r"[A-Za-z]", text)) >= 3
              and bottom < focus[1]):
            kind = "brand"
        if kind:
            accepted.append(dict(kind=kind, text=text, points=points, box=[left, top, right, bottom]))
    if sum(row["kind"] == "type" for row in accepted) != 1:
        return None
    brands = [row for row in accepted if row["kind"] == "brand"]
    if not 1 <= len(brands) <= 2:
        return None
    guide = trace_ellipse_border(source, color=color)
    if guide is None:
        return None
    center = np.asarray(guide.annulus[0][2][0])
    interior_points = np.rint(center + .92 * (guide.points-center)).astype(np.int32)
    interior = np.zeros((height, width), np.uint8)
    cv2.fillPoly(interior, [interior_points], 255)
    for row in accepted:
        # Centre brands belong close to the stamp centre; straight fragments
        # of the upper company arc must never become interior text to erase.
        midpoint = row["points"].mean(0)
        if row["kind"] == "brand" and np.linalg.norm(midpoint-center) > min(guide.annulus[0][2][1]) * .18:
            return None
        if any(cv2.pointPolygonTest(interior_points, tuple(point), False) <= 0 for point in row["points"]):
            return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    polygon_mask = np.zeros((height, width), np.uint8)
    for position, row in enumerate(sorted(accepted, key=lambda row: row["box"][1])):
        left, top, right, bottom = row["box"]
        lo = np.maximum(0, np.floor([left-6, top-6])).astype(int)
        hi = np.minimum([width, height], np.ceil([right+6, bottom+6])).astype(int)
        crop = cv2.copyMakeBorder(image[lo[1]:hi[1], lo[0]:hi[0]], 12, 12, 12, 12,
                                  cv2.BORDER_CONSTANT, value=(255, 255, 255))
        path = directory / f"seal-{index}-horizontal-{position}.png"
        if not cv2.imwrite(str(path), crop):
            raise ValueError("横排文字裁剪保存失败")
        reads = reader.read_line(path, provider=provider)
        actual = re.sub(r"\s+", "", "".join(read.text for read in reads))
        if (actual != row["text"] or not reads
                or any(not math.isfinite(read.confidence) or read.confidence < .95 for read in reads)):
            return None
        row.update(path=path, reads=[read.to_dict() for read in reads], points=row["points"].tolist())
        cv2.fillPoly(polygon_mask, [np.rint(row["points"]).astype(np.int32)], 255)
    selected = np.any(image < 255, axis=2) & (polygon_mask > 0) & (interior > 0)
    masked = image.copy()
    masked[selected] = 255
    target = directory / f"seal-{index}-horizontal-masked.png"
    mask_path = directory / f"seal-{index}-horizontal-mask.png"
    if not cv2.imwrite(str(target), masked) or not cv2.imwrite(str(mask_path), selected.astype(np.uint8)*255):
        raise ValueError("多行横排笔画填白图保存失败")
    return dict(rows=accepted, masked=target, mask=mask_path, guide=guide)
