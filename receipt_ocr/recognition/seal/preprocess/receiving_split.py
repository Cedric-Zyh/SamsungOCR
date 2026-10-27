"""Prepare literal name/code segments from one verified red-stamp row.

Only ink geometry selects the internal gap. Recognition receives real pixels,
and neither this module nor its contracts contain a requested name or code.
"""
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image, _write_stage_image


def measure_receiving_split(image):
    """Locate one natural gap, keeping Chinese radicals in their x interval."""
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3
            or min(image.shape[:2]) < 16):
        return {"prepared": False, "reason": "invalid_row_image"}
    height, width = image.shape[:2]
    pixels = image.astype(np.int16)
    chroma = np.maximum(0, pixels[:, :, 2] - np.maximum(pixels[:, :, 0], pixels[:, :, 1]))
    values = chroma[chroma > 4]
    threshold = max(5, min(20, float(np.percentile(values, 85)) * .15)) if values.size else 5
    mask = (chroma > threshold).astype(np.uint8)
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                  np.ones((1, max(20, round(width * .65))), np.uint8))
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                np.ones((max(15, round(height * .7)), 1), np.uint8))
    mask[(horizontal > 0) | (vertical > 0)] = 0
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    kept = [(int(x), int(y), int(x+w), int(y+h)) for x, y, w, h, area in stats[1:]
            if h >= max(7, height * .16) and area >= max(8, height**2 * .0015)
            and w < width * .8]
    intervals = []
    for x, y, right, bottom in sorted(kept):
        if intervals and x <= intervals[-1][2] + 1:
            left, top, previous_right, previous_bottom = intervals[-1]
            intervals[-1] = (left, min(top, y), max(previous_right, right), max(previous_bottom, bottom))
        else:
            intervals.append((x, y, right, bottom))
    result = {"prepared": False, "reason": "no_measured_internal_word_gap",
              "source_size": [width, height], "geometry_threshold": threshold,
              "ink_intervals": [list(box) for box in intervals], "eligible_gaps": []}
    if not intervals:
        return result
    glyph_height = float(np.median([bottom-top for _, top, _, bottom in kept]))
    gaps = [{"gap": b[0]-a[2], "x": (a[2]+b[0])//2}
            for a, b in zip(intervals, intervals[1:])
            if b[0]-a[2] >= glyph_height * .18
            and a[2]-intervals[0][0] >= width * .2
            and intervals[-1][2]-b[0] >= width * .15]
    result["eligible_gaps"] = gaps
    if not gaps:
        return result
    split = max(gaps, key=lambda item: item["gap"])["x"]
    left = min(box[0] for box in intervals)
    top = min(box[1] for box in intervals)
    right = max(box[2] for box in intervals)
    bottom = max(box[3] for box in intervals)
    result.update(prepared=True, reason="measured_same_row_word_gap",
                  left_box=[left, top, split, bottom], right_box=[split, top, right, bottom])
    return result


def prepare_receiving_split(color_path, gray_path, directory, index, prefix, row_index=0,
                            *, grid_verified=False):
    """Write six line inputs from a verified row, returning metadata and jobs.

    The caller supplies a complete physical row in two aligned image
    representations. A successful preparation is only permission to read the
    segments, not permission to replace an existing whole-row observation.
    """
    metadata = {"prepared": False, "reason": "requires_verified_physical_row",
                "grid_verified": grid_verified is True, "row_index": row_index, "inputs": []}
    if grid_verified is not True:
        return metadata, []
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in (index, row_index)):
        metadata["reason"] = "invalid_region_or_row_index"
        return metadata, []
    color, gray = _read_image(color_path), _read_image(gray_path)
    if color.shape != gray.shape:
        metadata["reason"] = "unaligned_row_representations"
        return metadata, []
    measured = measure_receiving_split(color)
    metadata.update(measured)
    if not measured["prepared"]:
        return metadata, []
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    jobs = []
    for representation, image in (("color", color), ("gray", gray)):
        for part, scale in (("left", 1), ("right", 1), ("right", 2)):
            box = measured[f"{part}_box"]
            left, top, right, bottom = box
            pixels = image[top:bottom, left:right]
            if scale == 2:
                pixels = cv2.resize(pixels, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            pixels = cv2.copyMakeBorder(pixels, 8, 8, 8, 8, cv2.BORDER_CONSTANT,
                                        value=(255, 255, 255))
            name = f"receiving-split-{row_index}-{representation}-{part}-{scale}"
            path = directory / f"seal-{index}-{name}.png"
            _write_stage_image(path, pixels, ".png")
            title = "仓库名段" if part == "left" else "完整代码段"
            view = "彩色" if representation == "color" else "色差灰度"
            item = {"id": f"seal-{index}:{name}", "name": name,
                    "label": f"收货章首行 · {title} · {view}" + (" · 2倍" if scale == 2 else ""),
                    "channel": "receiving_split", "read_mode": "line", "ocr_input": True,
                    "image_url": f"{prefix.rstrip('/')}/{path.name}" if prefix else "",
                    "rectangle_row_index": row_index, "representation": representation,
                    "split_part": part, "split_scale": scale, "crop_box": list(box),
                    "source_size": list(measured["source_size"]),
                    "coordinate_space": "receiving_row", "matching": False}
            metadata["inputs"].append(item)
            jobs.append((item, path))
    return metadata, jobs
