"""Locate the printed requirement line without reading neighboring fields."""
from __future__ import annotations

import re

import cv2
import numpy as np
from PIL import Image

from ...domain.ocr import TextObservation
from ...imaging.printed_page import suppress_stamp_color


_NEXT_FIELD = re.compile(r"^(?:实收数量|拒收数量|签收说明|发货单位|承运单位|收货客户)[:：]?")


def _requirement_line_rows(size, anchor, rows):
    """Join adjacent detections on the anchor's baseline, never the next row.

    The label/value, stamp number, station and phone can be separate boxes.
    Compare every candidate to the original anchor to avoid drifting into a
    lower line through a chain of slightly overlapping detections.
    """
    width, height = size
    selected = [anchor]
    right = anchor.x + anchor.width
    center_y = anchor.y + anchor.height / 2
    stops = [row.x for row in rows
             if row.x >= right and _NEXT_FIELD.match(re.sub(r"\s+", "", row.text))
             and abs(row.y + row.height / 2 - center_y)
             <= .45 * max(anchor.height, row.height)]
    stop_x = min(stops, default=1.0)
    for row in sorted(rows, key=lambda value: value.x):
        if row is anchor or not row.text.strip() or row.height <= 0:
            continue
        if _NEXT_FIELD.match(re.sub(r"\s+", "", row.text)):
            continue
        if row.x < anchor.x + min(anchor.width * .5, .08):
            continue
        if row.x + row.width <= right:
            continue
        if row.x + row.width > stop_x:
            continue
        if not .55 * anchor.height <= row.height <= 1.6 * anchor.height:
            continue
        overlap = min(anchor.y + anchor.height, row.y + row.height) - max(anchor.y, row.y)
        if overlap < .5 * min(anchor.height, row.height):
            continue
        if abs(row.y + row.height / 2 - center_y) > .45 * max(anchor.height, row.height):
            continue
        # Bound gaps in physical pixels, not in page-relative x/y units.
        if (row.x - right) * width > 3 * max(anchor.height, row.height) * height:
            continue
        selected.append(row)
        right = row.x + row.width
    return selected


def requirement_crop_box(size, anchor, rows):
    width, height = size
    y = anchor.y if anchor else .468
    row_height = anchor.height if anchor else .013
    left, right = .015 * width, .64 * width
    boundary = "template"
    if anchor:
        line = _requirement_line_rows(size, anchor, rows)
        line_right = max(row.x + row.width for row in line)
        y = min(row.y for row in line)
        row_height = max(row.y + row.height for row in line) - y
        value = re.sub(r"^签[章幸草]要求\s*[:：]?\s*", "", anchor.text).strip()
        # A complete detected field supplies its right edge; a label-only
        # detection still needs the wider search area for its separate value.
        bounded_line = len(value) >= 6 or len(line) > 1
        if bounded_line:
            right = line_right * width + max(8, row_height * height * .45)
            boundary = "detected_requirement_line" if len(line) > 1 else "detected_requirement_row"
        left = min(left, max(0, anchor.x * width - 8))
        for row in rows:
            label = re.sub(r"\s+", "", row.text)
            if not re.match(r"^(?:实收数量|拒收数量|签收说明)[:：]?", label):
                continue
            # Same/footer-neighbor rows can overlap the tilted requirement's
            # bounding rectangle. Only independent fields to its right bound x.
            if row.x <= anchor.x + min(anchor.width, .08):
                continue
            # A field on the next row may begin underneath a long requirement
            # (especially an authorization code). Never use that x coordinate
            # to cut through the already detected requirement itself.
            if bounded_line and row.x < line_right:
                continue
            if abs(row.y - y) > max(row_height, row.height) * 1.5:
                continue
            edge = row.x * width - max(4, row_height * height * .15)
            if left < edge < right:
                right, boundary = edge, "neighbor_field"
    box = (max(0, round(left)), max(0, round((y - .004) * height)),
           min(width, round(right)), min(height, round((y + row_height + .002) * height)))
    return box, boundary


def suppress_requirement_color(crop):
    return suppress_stamp_color(crop)


def extend_clean_requirement(image, anchor, page_rows, box, search_path, *, recognizer, backend):
    """Recover missing row extent after color suppression, before tight cropping.

    An incomplete page detector must not set the final right edge. Search a
    wider cleaned footer, then accept only an independently located requirement
    label on the original baseline and its adjacent same-line detections.
    This expands geometry, never supplies expected words or changes OCR text.
    """
    detect = getattr(recognizer, 'detect_boxes', None)
    if anchor is None or not callable(detect):
        return box, {'status': 'unavailable'}
    width, height = image.size
    margin_y = max(round(anchor.height * height), round(height * .008))
    search_box = (box[0], max(0, box[1] - margin_y),
                  min(width, max(box[2], round(width * .98))),
                  min(height, box[3] + margin_y))
    clean = suppress_requirement_color(image.crop(search_box))
    clean.save(search_path)
    scan_width, scan_height = clean.size
    rows = []
    for item in detect(search_path, backend=backend):
        points = np.asarray(item.get('points', []), dtype=float)
        confidence = float(item.get('confidence') or 0)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or not np.isfinite(confidence) or confidence < .80):
            continue
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        if left < 0 or top < 0 or right > scan_width or bottom > scan_height:
            continue
        rows.append(TextObservation(
            str(item.get('text', '')), confidence,
            (search_box[0] + left) / width, (search_box[1] + top) / height,
            (right-left) / width, (bottom-top) / height,
        ))
    candidates = [row for row in rows
                  if re.match(r'^签[章幸草]要求', row.text.strip())
                  and abs(row.x-anchor.x) * width <= 2 * anchor.height * height
                  and abs(row.y+row.height/2-anchor.y-anchor.height/2)
                  <= max(anchor.height, row.height) * .65]
    info = {'status': 'kept', 'search_box': list(search_box), 'previous_box': list(box)}
    if not candidates:
        info['reason'] = 'requirement_label_not_verified'
        return box, info
    # Keep separate page detections such as a phone/number as additional
    # geometry evidence. The fresh label is essential when its box now spans
    # the previously missing part of the field.
    cleaned_rows = [*page_rows, *rows]
    proposals = []
    for row in candidates:
        line = _requirement_line_rows(image.size, row, cleaned_rows)
        # A short label-only detection can invoke the old template width.
        # That default is not evidence of additional text: require an actual
        # verified row box to extend beyond the current reading margin.
        if max(part.x + part.width for part in line) * width <= box[2]:
            continue
        proposals.append(requirement_crop_box(image.size, row, cleaned_rows)[0])
    if not proposals:
        info['reason'] = 'no_additional_extent'
        return box, info
    proposed = max(proposals, key=lambda value: value[2])
    # This pass repairs missing extent only. A shorter cleaned detection must
    # not replace a previously verified station, phone or terminal number.
    if proposed[2] <= box[2]:
        info['reason'] = 'no_additional_extent'
        return box, info
    expanded = (min(box[0], proposed[0]), min(box[1], proposed[1]),
                proposed[2], max(box[3], proposed[3]))
    info.update(status='extended', crop_box=list(expanded))
    return expanded, info


def clean_and_deskew_requirement(crop):
    """Suppress stamp color, fit a supported text baseline, then crop that row.

    Geometry uses glyph components only; neither the expected company name
    nor recognized business text is used to manufacture or truncate a value.
    Without enough baseline evidence retain the bounded original crop.
    """
    clean = suppress_requirement_color(crop)
    image = np.asarray(clean)
    height, width = image.shape
    meta = {"rotation_degrees": 0.0, "deskew_applied": False}
    if height < 12 or width < 80:
        return clean, meta
    _, _, stats, _ = cv2.connectedComponentsWithStats((image < 150).astype(np.uint8))
    glyphs = np.array([s for s in stats[1:] if s[4] > 10
                      and .25 * height < s[3] < .85 * height and s[2] < 2 * s[3]])
    if len(glyphs) < 5:
        return clean, meta
    centers = glyphs[:, 0] + glyphs[:, 2] / 2
    bottoms = glyphs[:, 1] + glyphs[:, 3]
    slopes = [(bottoms[j] - bottoms[i]) / (centers[j] - centers[i])
              for i in range(len(glyphs)) for j in range(i + 1, len(glyphs))
              if abs(centers[j] - centers[i]) > 2 * height]
    if len(slopes) < 6:
        return clean, meta
    slope = float(np.median(slopes))
    glyph_height = float(np.median(glyphs[:, 3]))
    intercepts = bottoms - slope * centers
    inliers = abs(intercepts - np.median(intercepts)) <= max(3, glyph_height * .15)
    glyphs = glyphs[inliers]
    if len(glyphs) < 5 or np.ptp(centers[inliers]) < .45 * width:
        return clean, meta
    angle = float(np.degrees(np.arctan(slope)))
    if abs(angle) > 6:
        return clean, meta
    # Expand the canvas, so rotation cannot cut off a leading glyph/number.
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    co, si = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(np.ceil(width * co + height * si))
    new_height = int(np.ceil(height * co + width * si))
    matrix[:, 2] += [(new_width - width) / 2, (new_height - height) / 2]
    rotated = cv2.warpAffine(image, matrix, (new_width, new_height),
                             flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    corners = np.array([(x + dx, y + dy, 1) for x, y, w, h, _ in glyphs
                        for dx, dy in [(0, 0), (w, 0), (w, h), (0, h)]])
    mapped = corners @ matrix.T
    pad = max(5, round(glyph_height * .20))
    top = max(0, int(np.floor(mapped[:, 1].min())) - pad)
    bottom = min(new_height, int(np.ceil(mapped[:, 1].max())) + pad)
    rotated = rotated[top:bottom]
    # Keep breathing room for the recognizer, including brackets and digits.
    rotated = cv2.copyMakeBorder(rotated, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
    meta.update(rotation_degrees=round(angle, 3), deskew_applied=True,
                baseline_glyph_count=len(glyphs), line_bounds=[top, bottom])
    return Image.fromarray(rotated), meta
