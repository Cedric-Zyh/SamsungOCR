"""Measure complete receiving-form grids without using requested text or OCR.

Long horizontal rules and independently observed side extents must agree.
Broken ink is aggregated only for geometry; recognition images retain the
original colour pixels and a continuous red-chroma representation.
"""
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image, _write_stage_image
from .orientation_geometry import _rotation_geometry


_MEASUREMENT_WIDTH = 1000
_CHROMA_CUTOFF = 4


def _reject(reason, **metrics):
    return {"accepted": False, "reason": reason, "metrics": metrics}


def _hough(mask, threshold, length, gap):
    found = cv2.HoughLinesP(mask * 255, 1, np.pi / 720, threshold=threshold,
                            minLineLength=length, maxLineGap=gap)
    return [] if found is None else found[:, 0]


def _rotation_angle(mask):
    width = mask.shape[1]
    angles = []
    for x, y, right, bottom in _hough(mask, 80, width * .40, width * .04):
        angle = (np.degrees(np.arctan2(float(bottom-y), float(right-x))) + 90) % 180 - 90
        if abs(angle) <= 25:
            angles.append((float(angle), float(np.hypot(right-x, bottom-y))))
    if len(angles) < 3:
        return None
    center = max(angles, key=lambda a: sum(length for angle, length in angles
                                           if abs(angle-a[0]) < 1.5))[0]
    close = [(angle, length) for angle, length in angles if abs(angle-center) < 1.5]
    return float(np.average([a for a, _ in close], weights=[n for _, n in close]))


def _paired_sides(mask):
    height, width = mask.shape
    segments = []
    for x, y, right, bottom in _hough(mask, 65, height * .12, height * .035):
        x, y, right, bottom = map(float, (x, y, right, bottom))
        if bottom < y:
            x, y, right, bottom = right, bottom, x, y
        if bottom-y < height * .12 or abs(right-x) > max(4, (bottom-y) * .06):
            continue
        center_x = x + (right-x) * (height * .5-y) / max(1, bottom-y)
        segments.append((center_x, y, bottom))
    clusters = []
    for segment in sorted(segments):
        if not clusters or segment[0]-np.median([s[0] for s in clusters[-1]]) > width * .025:
            clusters.append([segment])
        else:
            clusters[-1].append(segment)
    sides = []
    for cluster in clusters:
        support = np.zeros(height, np.uint8)
        for _, top, bottom in cluster:
            support[max(0, int(top)):min(height, int(bottom)+1)] = 1
        if support.mean() >= .25:
            ys = np.flatnonzero(support)
            sides.append({"x": float(np.median([s[0] for s in cluster])),
                          "top": int(ys.min()), "bottom": int(ys.max()),
                          "coverage": float(support.mean())})
    pairs = []
    for left, right in combinations(sides, 2):
        span = right["x"]-left["x"]
        top, bottom = min(left["top"], right["top"]), max(left["bottom"], right["bottom"])
        overlap = min(left["bottom"], right["bottom"])-max(left["top"], right["top"])
        if (span >= width * .55 and bottom-top >= span * .28
                and overlap >= (bottom-top) * .30
                and max(left["coverage"], right["coverage"]) >= .50):
            pairs.append((span * (bottom-top) * (left["coverage"]+right["coverage"]), left, right))
    return (max(pairs, key=lambda p: p[0])[1:] if pairs else None), sides


def _spanning_rules(mask, sides):
    height, image_width = mask.shape
    left, right = [int(round(side["x"])) for side in sides]
    width = right-left
    top, bottom = min(s["top"] for s in sides), max(s["bottom"] for s in sides)
    span_height = bottom-top
    segments = []
    for x, y, end, end_y in _hough(mask, 70, width * .35, width * .045):
        x, y, end, end_y = map(float, (x, y, end, end_y))
        if end < x:
            x, y, end, end_y = end, end_y, x, y
        if end-x < width * .35 or abs(end_y-y) > max(3, (end-x) * .055):
            continue
        center_y = y + (end_y-y) * ((left+right) * .5-x) / max(1, end-x)
        if top-span_height * .04 <= center_y <= bottom+span_height * .04:
            segments.append((center_y, x, end, y, end_y))
    clusters = []
    for segment in sorted(segments):
        if not clusters or segment[0]-np.median([s[0] for s in clusters[-1]]) > max(8, span_height * .025):
            clusters.append([segment])
        else:
            clusters[-1].append(segment)
    levels = []
    for cluster in clusters:
        support = np.zeros(image_width, np.uint8)
        for _, start, end, _, _ in cluster:
            support[max(0, int(start)):min(image_width, int(end)+1)] = 1
        xs = np.flatnonzero(support)
        low = min(min(s[3], s[4]) for s in cluster)
        high = max(max(s[3], s[4]) for s in cluster)
        center = float(np.median([s[0] for s in cluster]))
        internal = top+span_height*.10 < center < bottom-span_height*.10
        # A pale internal divider can stop short of a side. Allow that small
        # gap only with stronger total support; outer rules stay unchanged.
        if not len(xs) or high-low > span_height*.08:
            continue
        coverage = float(support[left:right].mean())
        ordinary = xs.min() <= left+width*.09 and xs.max() >= right-width*.09 and coverage >= .74
        bounded_internal = (internal and xs.min() <= left+width*.12
                            and xs.max() >= right-width*.12 and coverage >= .85)
        if not (ordinary or bounded_internal):
            continue
        levels.append({"y": center,
                       "band": [int(np.floor(low)), int(np.ceil(high))+1],
                       "left": int(xs.min()), "right": int(xs.max()),
                       "support_fraction": float(support[left:right].mean())})
    return levels, [left, top, right, bottom]


def _complete_rules(levels, outer_box):
    _, top, _, bottom = outer_box
    height = bottom-top
    if not 4 <= len(levels) <= 32:
        return None
    first = min(range(len(levels)), key=lambda i: abs(levels[i]["y"]-top))
    last = min(range(len(levels)), key=lambda i: abs(levels[i]["y"]-bottom))
    if (first >= last or abs(levels[first]["y"]-top) > height * .10
            or abs(levels[last]["y"]-bottom) > height * .10):
        return None
    levels = levels[first:last+1]
    choices = []
    for count in range(4, min(7, len(levels))+1):
        for inside in combinations(range(1, len(levels)-1), count-2):
            selected = [levels[i] for i in (0, *inside, len(levels)-1)]
            gaps = np.diff([line["y"] for line in selected])
            mean = float(gaps.mean())
            if mean <= 0 or gaps.min() < mean * .60 or gaps.max() > mean * 1.50:
                continue
            variation = float(gaps.std()/mean)
            score = sum(line["support_fraction"] for line in selected)/count-variation * 2
            choices.append((score, selected, variation))
    return max(choices, key=lambda choice: choice[0]) if choices else None


def _fragmented_internal_rules(mask, sides):
    """Verify collinear short segments against untouched ink across the row.

    Outer rules and side lines still require their ordinary measurements.
    Short glyph strokes alone cannot supply the broad, thin ink support of
    a divider. No gaps are filled in either geometry or recognition pixels.
    """
    left, right = [int(round(side['x'])) for side in sides]
    width = right-left
    top, bottom = min(s['top'] for s in sides), max(s['bottom'] for s in sides)
    span = bottom-top
    segments = []
    for x, y, end, end_y in _hough(mask, 30, width*.08, width*.018):
        x, y, end, end_y = map(float, (x, y, end, end_y))
        if end < x:
            x, y, end, end_y = end, end_y, x, y
        if end-x < width*.08 or abs(end_y-y) > max(2, (end-x)*.025):
            continue
        center = y+(end_y-y)*((left+right)*.5-x)/(end-x)
        if top+span*.10 < center < bottom-span*.10:
            segments.append((center, x, end, y, end_y))
    clusters = []
    for segment in sorted(segments):
        if not clusters or segment[0]-np.median([s[0] for s in clusters[-1]]) > max(6, span*.02):
            clusters.append([segment])
        else:
            clusters[-1].append(segment)
    levels = []
    columns = np.arange(left, right, dtype=np.float32)
    half_band = max(3, round(span*.01))
    offsets = np.arange(-half_band, half_band+1, dtype=np.float32)
    for cluster in clusters:
        union = np.zeros(mask.shape[1], np.uint8)
        for _, x, end, _, _ in cluster:
            union[max(0, int(x)):min(mask.shape[1], int(end)+1)] = 1
        xs = np.flatnonzero(union)
        if union[left:right].mean() < .60 or xs[-1]-xs[0] < width*.70:
            continue
        center = float(np.median([s[0] for s in cluster]))
        slope = float(np.median([(s[4]-s[3])/(s[2]-s[1]) for s in cluster]))
        line = center+slope*(columns-(left+right)*.5)
        map_x = np.broadcast_to(columns, (len(offsets), len(columns))).copy()
        map_y = (line[None, :]+offsets[:, None]).astype(np.float32)
        observed = cv2.remap(mask, map_x, map_y, cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0).any(axis=0)
        edge = max(1, round(width*.10))
        longest = gap = 0
        for present in observed:
            gap = 0 if present else gap+1
            longest = max(longest, gap)
        if (observed.mean() < .80 or min(observed[:edge].mean(), observed[-edge:].mean()) < .20
                or longest > width*.18):
            continue
        low = min(min(s[3], s[4]) for s in cluster)
        high = max(max(s[3], s[4]) for s in cluster)
        if high-low > span*.08:
            continue
        levels.append({'y': center, 'band': [int(np.floor(low)), int(np.ceil(high))+1],
                       'left': int(xs[0]), 'right': int(xs[-1]),
                       'support_fraction': float(observed.mean()),
                       'method': 'observed_collinear_fragments'})
    return levels


def measure_receiving_grid(image):
    """Return a complete measured grid, or a conservative rejection.

    ``frame_box`` and ``row_boxes`` use the returned, 1000-pixel-wide-before-
    rotation image coordinate system. The transform maps original input
    coordinates directly into that image. No OCR or requested text is used.
    """
    if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
            or image.dtype != np.uint8 or min(image.shape[:2]) < 30):
        return _reject("invalid_colour_image")
    source_height, source_width = image.shape[:2]
    target_height = round(source_height * _MEASUREMENT_WIDTH/source_width)
    if not 100 <= target_height <= 4000:
        return _reject("unsupported_grid_aspect")
    reference = cv2.resize(image, (_MEASUREMENT_WIDTH, target_height))
    pixels = reference.astype(np.int16)
    chroma = np.clip(pixels[:, :, 2]-np.maximum(pixels[:, :, 0], pixels[:, :, 1]), 0, 255).astype(np.uint8)
    angle = _rotation_angle((chroma > _CHROMA_CUTOFF).astype(np.uint8))
    if angle is None or not np.isfinite(angle):
        return _reject("insufficient_horizontal_segments")
    matrix, width, height = _rotation_geometry(_MEASUREMENT_WIDTH, target_height, angle)
    rotated = cv2.warpAffine(reference, matrix, (width, height), flags=cv2.INTER_CUBIC,
                             borderValue=(255, 255, 255))
    chroma = cv2.warpAffine(chroma, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)
    mask = (chroma > _CHROMA_CUTOFF).astype(np.uint8)
    sides, all_sides = _paired_sides(mask)
    if sides is None:
        return _reject("missing_paired_side_lines", angle=angle, sides=all_sides)
    positions = [s["x"] for s in sides]
    if not all(np.isfinite(positions)) or not 0 <= positions[0] < positions[1] < width:
        return _reject("invalid_measured_side_extent", angle=angle)
    levels, outer = _spanning_rules(mask, sides)
    complete = _complete_rules(levels, outer)
    fragmented_recovery = False
    if complete is None:
        # Add independently verified internal dividers only. In particular,
        # a missing top/bottom rule cannot be manufactured by this fallback.
        additions = _fragmented_internal_rules(mask, sides)
        for candidate in additions:
            if all(abs(candidate['y']-line['y']) > (outer[3]-outer[1])*.04 for line in levels):
                levels.append(candidate)
        levels.sort(key=lambda line: line['y'])
        complete = _complete_rules(levels, outer)
        fragmented_recovery = complete is not None
    if complete is None:
        return _reject("incomplete_outer_grid_rules", angle=angle, levels=levels, sides=sides)
    _, levels, variation = complete
    left, right = outer[0]+5, outer[2]-5
    top, bottom = levels[0]["band"][0]+5, levels[-1]["band"][1]-5
    frame = [left, top, right, bottom]
    rows = [[left, max(top, a["band"][1]+2), right, min(bottom, b["band"][0]-2)]
            for a, b in zip(levels, levels[1:])]
    boxes = [frame, *rows]
    if any(not (0 <= l < r <= width and 0 <= t < b <= height) for l, t, r, b in boxes):
        return _reject("invalid_measured_grid_extent", angle=angle)
    transform = matrix @ np.diag([_MEASUREMENT_WIDTH/source_width, target_height/source_height, 1])
    metrics = {"angle": angle, "levels": levels, "side_lines": list(sides),
               "side_supports": [s["coverage"] for s in sides], "gap_cv": variation,
               "measurement_width": _MEASUREMENT_WIDTH, "chroma_cutoff": _CHROMA_CUTOFF,
               "fragmented_rule_recovery": fragmented_recovery}
    return {"accepted": True, "reason": "complete_four_sided_receiving_grid", "metrics": metrics,
            "image": rotated, "chroma": chroma, "angle": angle, "frame_box": frame,
            "row_boxes": rows, "source_to_rotated_matrix": transform.tolist(),
            "source_size": [source_width, source_height]}


def prepare_receiving_grid(source, directory, index, prefix=""):
    """Persist the exact same complete cells in colour and continuous gray.

    Whole images have no padding. ``rows.box`` is relative to the tight whole
    image; ``image_box`` is in the measured image frame. Both row input images
    add eight white pixels on every side. This helper performs no OCR.
    """
    measured = measure_receiving_grid(_read_image(source))
    if not measured["accepted"]:
        return measured
    directory = Path(directory)
    left, top, right, bottom = measured["frame_box"]
    chroma = measured["chroma"]
    ink = chroma[top:bottom, left:right]
    high = float(np.percentile(ink[ink > 4], 99)) if np.any(ink > 4) else 255
    gray = np.clip(255-chroma.astype(float)*230/max(20, high), 0, 255).astype(np.uint8)
    def save(name, pixels):
        path = directory / f"seal-{index}-receiving-grid-{name}.png"
        _write_stage_image(path, pixels, ".png")
        return str(path), f"{prefix.rstrip('/')}/{path.name}" if prefix else ""
    color_path, color_url = save("whole-color", measured["image"][top:bottom, left:right])
    gray_path, gray_url = save("whole-gray", gray[top:bottom, left:right])
    result = {key: value for key, value in measured.items() if key not in {"image", "chroma"}}
    result.update(whole_color_path=color_path, whole_color_url=color_url, whole_gray_path=gray_path,
                  whole_gray_url=gray_url, whole_frame_crop_box=[left, top, right, bottom],
                  coordinate_space="whole tight frame without padding", rows=[])
    for number, (l, t, r, b) in enumerate(measured["row_boxes"]):
        row = {"index": number, "box": [l-left, t-top, r-left, b-top],
               "image_box": [l, t, r, b], "padding": 8}
        for kind, image in [("color", measured["image"]), ("gray", gray)]:
            crop = cv2.copyMakeBorder(image[t:b, l:r], 8, 8, 8, 8, cv2.BORDER_CONSTANT,
                                      value=(255, 255, 255) if kind == "color" else 255)
            row[f"{kind}_path"], row[f"{kind}_url"] = save(f"row-{number}-{kind}", crop)
        result["rows"].append(row)
    return result
