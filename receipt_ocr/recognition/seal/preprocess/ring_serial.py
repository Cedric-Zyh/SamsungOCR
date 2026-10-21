"""Read small, oppositely oriented ring digits without changing company OCR."""
import math
from pathlib import Path
import re

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image, _write_stage_image
from receipt_ocr.imaging.unwrap import save_unwrapped_seal


def _serial_groups(image, *, anchors_only=False):
    """Group neighbouring small glyphs before judging the complete serial row.

    Height and spacing gates use the local glyph size, not a fixed fraction
    of the full strip height. Keep isolated Chinese upper strokes out by
    requiring consistent spacing, a common baseline, and blank space below.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    ink = (gray < 180).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components = [tuple(map(int, row)) for row in stats[1:] if row[4] >= 12]
    if sum(h >= height * .5 and w >= height * .2 for x, y, w, h, area in components) < 3:
        return []
    small = sorted(row for row in components
                   if max(6, height * .06) <= row[3] <= height * .4
                   and row[1] + row[3] <= height * .5
                   and .10 * row[3] <= row[2] <= 1.25 * row[3]
                   and row[4] >= .06 * row[3] ** 2)
    parents = list(range(len(small)))

    def find(index):
        while parents[index] != index:
            index = parents[index]
        return index

    for index, (x, y, w, h, area) in enumerate(small):
        for other in range(index + 1, len(small)):
            xx, yy, ww, hh, other_area = small[other]
            unit = (h + hh) / 2
            gap = xx - (x + w)
            if gap > height * .4:
                break
            if (max(h, hh) <= 1.6 * min(h, hh) and -.1 * unit <= gap <= .95 * unit
                    and abs(y + h / 2 - yy - hh / 2) <= .30 * unit):
                parents[find(other)] = find(index)
    groups = {}
    for index, row in enumerate(small):
        groups.setdefault(find(index), []).append(row)
    result = []
    for group in groups.values():
        if not (4 if anchors_only else 6) <= len(group) <= 24:
            continue
        heights = np.array([row[3] for row in group])
        unit = float(np.median(heights))
        if heights.max() > 1.6 * heights.min():
            continue
        centers = np.array([row[0] + row[2] / 2 for row in group])
        pitch = np.diff(centers)
        if np.max(np.abs(pitch - np.median(pitch))) > .4 * np.median(pitch):
            continue
        if np.ptp([row[1] + row[3] for row in group]) > .4 * unit:
            continue
        padding = max(3, round(unit * .12))
        left = max(0, min(row[0] for row in group) - padding)
        right = min(width, max(row[0] + row[2] for row in group) + padding)
        top = min(row[1] for row in group)
        bottom = max(row[1] + row[3] for row in group)
        below = ink[min(height, bottom + padding):, left:right]
        if not below.size or (not anchors_only and below.mean() > .005):
            continue
        if left == 0 or right == width or right - left > width * .45:
            continue
        result.append({"box": [left, top, right, bottom],
                       "crop_box": [left, max(0, top - padding), right,
                                    min(height, bottom + padding)],
                       "glyph_count": len(group), "median_height": unit,
                       "min_height": int(heights.min()), "max_height": int(heights.max())})
        if anchors_only:
            result[-1]["glyphs"] = group
    return result


def _supported_serial_row(image):
    """Locate a noisy row using two independently regular glyph runs.

    A six-glyph run sets height, pitch and baseline; a separate four-glyph
    run must agree. Extend only through occupied glyph-sized cells and stop
    at blank space. This is localization only: OCR receives untouched pixels
    and must still return exactly the observed number of cells as digits.
    """
    height, width = image.shape[:2]
    candidates = []
    for shift in (0, width // 2):
        shifted = np.roll(image, shift, axis=1)
        ink = cv2.cvtColor(shifted, cv2.COLOR_BGR2GRAY) < 180
        groups = _serial_groups(shifted, anchors_only=True)
        for seed in groups:
            if seed["glyph_count"] < 6:
                continue
            glyphs = seed["glyphs"]
            xs = np.array([x + w / 2 for x, y, w, h, area in glyphs])
            ys = np.array([y + h / 2 for x, y, w, h, area in glyphs])
            pitch = float(np.median(np.diff(xs)))
            unit = seed["median_height"]
            slope, intercept = np.polyfit(xs, ys, 1)
            if not .65 * unit <= pitch <= 1.8 * unit or abs(slope) > .15:
                continue
            origin = float(np.median(xs - np.arange(len(xs)) * pitch))
            supporting = []
            for group in groups:
                if group is seed or not .8 * unit <= group["median_height"] <= 1.25 * unit:
                    continue
                positions = []
                for x, y, w, h, area in group["glyphs"]:
                    xx, yy = x + w / 2, y + h / 2
                    index = round((xx - origin) / pitch)
                    if (abs(xx - (origin + index * pitch)) > .3 * pitch
                            or abs(yy - (slope * xx + intercept)) > .3 * unit):
                        break
                    positions.append(index)
                else:
                    supporting.extend(positions)
            if len(supporting) < 4:
                continue

            def cell(index):
                center = origin + index * pitch
                middle = slope * center + intercept
                box = [round(center - .49 * pitch), round(middle - .68 * unit),
                       round(center + .49 * pitch), round(middle + .68 * unit)]
                left, top, right, bottom = box
                if left <= 0 or top < 0 or right >= width or bottom > height * .55:
                    return None
                yy, xx = np.where(ink[top:bottom, left:right])
                if (len(xx) < .10 * unit ** 2 or np.ptp(yy) < .55 * unit
                        or np.ptp(xx) < .10 * unit):
                    return None
                return box

            lo, hi = 0, len(glyphs) - 1
            while hi - lo + 1 <= 24 and cell(lo - 1) is not None:
                lo -= 1
            while hi - lo + 1 <= 24 and cell(hi + 1) is not None:
                hi += 1
            if not 10 <= hi - lo + 1 <= 24 or not all(lo <= i <= hi for i in supporting):
                continue
            boxes = [cell(i) for i in range(lo, hi + 1)]
            if any(box is None for box in boxes):
                continue
            left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
            right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
            padding = max(2, round(unit * .1))
            left, right = max(0, left - padding), min(width, right + padding)
            if right - left > width * .45:
                continue
            candidates.append({"box": [left, top, right, bottom],
                               "crop_box": [left, top, right, bottom],
                               "glyph_count": hi - lo + 1, "median_height": unit,
                               "min_height": seed["min_height"], "max_height": seed["max_height"],
                               "shift": shift, "stroke_join_height": 0,
                               "localization": "two_regular_runs", "pitch": pitch,
                               "supporting_glyphs": seed["glyph_count"] + len(supporting)})
    return max(candidates, key=lambda item: item["glyph_count"], default=None)


def locate_ring_serial(image):
    """Join a whole run across the circular seam without changing company OCR."""
    candidates = []
    for shift in (0, image.shape[1] // 2):
        for group in _serial_groups(np.roll(image, shift, axis=1)):
            candidates.append({**group, "shift": shift, "stroke_join_height": 0})
    if candidates:
        # A partial but regular row supplies its measured glyph height. Close
        # only short vertical gaps on a separate localization mask. One-column
        # kernels cannot bridge the horizontal gap between adjacent digits.
        # OCR always crops the untouched source, never this auxiliary mask.
        seed = max(candidates, key=lambda item: item["glyph_count"])
        ink = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 180).astype(np.uint8) * 255
        kernels = sorted({max(3, round(seed["median_height"] * ratio)) for ratio in (.18, .28, .38)})
        for size in kernels:
            if size % 2 == 0:
                size += 1
            joined = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((size, 1), np.uint8))
            guide = cv2.cvtColor(255 - joined, cv2.COLOR_GRAY2BGR)
            for shift in (0, image.shape[1] // 2):
                for group in _serial_groups(np.roll(guide, shift, axis=1)):
                    if group["glyph_count"] > seed["glyph_count"]:
                        candidates.append({**group, "shift": shift, "stroke_join_height": size})
    if candidates:
        return max(candidates, key=lambda item: item["glyph_count"])
    return _supported_serial_row(image)


def read_ring_serial(source, directory, index, prefix, artifact, *, reader, provider):
    """Record a separate serial evidence channel, never company-match text."""
    directory = Path(directory)
    full = directory / f"seal-{index}-serial-ring-full.png"
    save_unwrapped_seal(source, full, None, color=artifact["color"],
                        elliptical=True, normalized=True, single_line=True,
                        preserve_full_ring=True)
    image = _read_image(full)
    selection = locate_ring_serial(image)
    artifact["ring_serial_detection"] = {"status": "not_detected"}
    if selection is None:
        return

    def add(name, path, label, *, ocr=False):
        url = f"{prefix.rstrip('/')}/{path.name}" if prefix else ""
        item = {"id": f"seal-{index}:{name}", "name": name, "label": label,
                "channel": "serial" if ocr else None, "image_url": url,
                "ocr_input": ocr}
        artifact["inputs"].append(item)
        return item

    full_input = add("serial-ring-full", full, "编号定位用完整圆环")
    artifact["ring_serial_full_url"] = full_input["image_url"]
    shifted = np.roll(image, selection["shift"], axis=1)
    left, top, right, bottom = selection["crop_box"]
    crop = shifted[top:bottom, left:right]
    detection = {"status": "unreadable", "method": "relative_glyph_row_two_directions",
                 "source_size": [image.shape[1], image.shape[0]],
                 **selection, "candidates": []}
    artifact["ring_serial_detection"] = detection
    candidates = []
    for rotation in (0, 180):
        upright = cv2.rotate(crop, cv2.ROTATE_180) if rotation else crop
        upright = cv2.copyMakeBorder(upright, 10, 10, 10, 10,
                                    cv2.BORDER_CONSTANT, value=(255, 255, 255))
        path = directory / f"seal-{index}-serial-{rotation}.png"
        _write_stage_image(path, upright, ".png")
        entry = add(f"serial-{rotation}", path, f"环形编号 · 方向 {rotation}°", ocr=True)
        observations = reader.read_line(path, provider=provider)
        entry["status"] = "completed" if observations else "empty"
        for row in observations:
            raw_text = str(row.text or "").strip()
            # OCR may insert spaces between widely spaced digits on the arc.
            # Preserve the raw read for review; normalize whitespace only.
            text = re.sub(r"\s+", "", raw_text)
            confidence = float(row.confidence or 0)
            detection["candidates"].append({"text": raw_text, "normalized_text": text,
                                             "confidence": confidence,
                                             "rotation": rotation,
                                             "image_url": entry["image_url"]})
            # Do not turn letters into digits, pad a length, or use a known code.
            if (re.fullmatch(r"[0-9]{6,24}", text) and math.isfinite(confidence)
                    and confidence >= .90 and len(text) == selection["glyph_count"]):
                candidates.append((confidence, text, rotation, entry))
    if not candidates:
        return
    candidates.sort(key=lambda item: item[0], reverse=True)
    confidence, text, rotation, entry = candidates[0]
    if any(other[1] != text and confidence - other[0] < .05 for other in candidates[1:]):
        detection["status"] = "ambiguous"
        return
    detection.update(status="recognized", rotation=rotation)
    artifact.update(ring_serial_text=text, ring_serial_confidence=confidence,
                    ring_serial_url=entry["image_url"])
    artifact["reads"].append({"id": f"{entry['id']}:0", "input_id": entry["id"],
                              "region_id": artifact["region_id"], "channel": "serial",
                              "provider": provider, "text": text, "confidence": confidence,
                              "matching": False,
                              "image_url": entry["image_url"]})
