"""Recover a missing number parenthesis only from nearby observed source ink."""
import math
from pathlib import Path
import re
import shutil
import tempfile

import cv2
import numpy as np

from receipt_ocr.imaging.crops import seal_source_reference
from .number import MIN_NUMBER_CONFIDENCE, prepare_number_input, seal_number
from .orientation_geometry import _rotation_geometry


def _missing_side(text):
    text = re.sub(r"\s+", "", str(text or "")).translate(str.maketrans("（）", "()"))
    if re.fullmatch(r"\([0-9０-９]{1,3}", text):
        return "right"
    if re.fullmatch(r"[0-9０-９]{1,3}\)", text):
        return "left"
    return None


def _aligned_reference(source, region, rotations):
    reference = seal_source_reference(source, region)
    # Replay each actual rotation, including expanded canvases. Adding the
    # angles would misalign pixels after a coarse and a fine rotation.
    for angle in rotations:
        if not math.isfinite(angle):
            raise ValueError("编号原图方向数据无效")
        h, w = reference.shape[:2]
        matrix, width, height = _rotation_geometry(w, h, angle)
        reference = cv2.warpAffine(reference, matrix, (width, height),
                                   flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return reference


def _adjacent_component(reference, detection, side):
    """One same-baseline glyph beside the known number, never a rectangle copy."""
    left, top, right, bottom = map(int, detection["box"])
    sl, st, sr, sb = map(int, detection["search_box"])
    height = bottom - top
    x1 = max(sl, math.floor(left - height * .8)) if side == "left" else max(sl, right - 4)
    x2 = min(sr, math.ceil(right + height * .8)) if side == "right" else min(sr, left + 4)
    y1, y2 = max(st, math.floor(top - height * .2)), min(sb, math.ceil(bottom + height * .2))
    crop = reference[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    darkness = 255 - crop.max(axis=2)
    # The low threshold keeps antialiased grey ink. It only proposes geometry;
    # both OCR passes must corroborate the number before any pixels are saved.
    _, labels, stats, _ = cv2.connectedComponentsWithStats((darkness >= 16).astype(np.uint8), 8)
    candidates = []
    for label, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if (not height * .6 <= h <= height * 1.25
                or not height * .08 <= w <= height * .6
                or area < height * height * .025
                or x <= 0 or y <= 0 or x + w >= crop.shape[1] or y + h >= crop.shape[0]
                or abs(y1 + y + h / 2 - (top + bottom) / 2) > height * .25):
            continue
        gap = x1 + x - right if side == "right" else left - (x1 + x + w)
        if not -4 <= gap <= height * .45:
            continue
        component = labels == label
        # Neutral black cores and saturated unrelated ink are ineligible.
        pixels = crop[component].astype(np.int16)
        if np.median(pixels.max(axis=1)) < 115 or np.median(np.ptp(pixels, axis=1)) > 24:
            continue
        mask = np.zeros(reference.shape[:2], dtype=bool)
        mask[y1:y2, x1:x2] = component
        candidates.append((mask, [int(x1+x), int(y1+y), int(x1+x+w), int(y1+y+h)]))
    # Ambiguity is left for review; do not choose the glyph that matches a
    # requested number, or search the page/another stamp for a replacement.
    return candidates[0] if len(candidates) == 1 else None


def recover_number_punctuation(source, region, working, destination, number_path,
                               original_number_path, focus, orientation, detection,
                               *, reader, provider):
    """Return new detection + audit data, leaving all files unchanged on rejection."""
    side = _missing_side(detection.get("text"))
    if (region.color != "red" or detection.get("status") != "detected" or not side
            or not detection.get("box") or not detection.get("search_box")):
        return detection, {"status": "not_needed"}
    rotations = orientation.get("rotation_steps")
    if rotations is None:
        return detection, {"status": "unavailable", "reason": "缺少原图方向映射"}
    reference = _aligned_reference(source, region, rotations)
    clean = cv2.imread(str(working), cv2.IMREAD_COLOR)
    if clean is None or clean.shape != reference.shape:
        return detection, {"status": "unavailable", "reason": "原图与章色图坐标不一致"}
    component = _adjacent_component(reference, detection, side)
    if component is None:
        return detection, {"status": "not_recovered", "reason": "原图相邻笔画不足或有歧义"}
    mask, component_box = component
    # Only observed pixels in the one verified component can change. Preserve
    # their soft luminance in the same red-on-white rendering as other ink.
    strength = np.clip((255 - reference.max(axis=2).astype(np.float32)) * 1.2, 0, 225)
    restored = clean.copy()
    for channel in (0, 1):
        restored[mask, channel] = np.minimum(clean[mask, channel], np.rint(255-strength[mask]).astype(np.uint8))
    restored[mask, 2] = 255
    with tempfile.TemporaryDirectory(prefix="receipt-number-recovery-") as scratch:
        full, number = Path(scratch) / "stamp.png", Path(scratch) / "number.png"
        if not cv2.imwrite(str(full), restored):
            raise ValueError("编号笔画候选图保存失败")
        proposed = prepare_number_input(full, number, focus, reader=reader, provider=provider)
        rows = reader.read_line(number, provider=provider) if proposed.get("status") == "detected" else []
        texts = [proposed.get("text", "")] + [row.text for row in rows]
        scores = [float(proposed.get("confidence") or 0)] + [float(row.confidence) for row in rows]
        wanted = seal_number(detection["text"])
        if (len(rows) != 1 or any(not math.isfinite(s) or s < MIN_NUMBER_CONFIDENCE for s in scores)
                or any(seal_number(text) != wanted or not re.fullmatch(r"[（(][0-9０-９]{1,3}[）)]", text)
                       for text in texts)):
            return detection, {"status": "not_recovered", "reason": "原图笔画未通过编号双重识别",
                               "proposed_texts": texts, "component_box": component_box}
        # Number image, full standard-seal candidate and audit image all refer
        # to the same pixels. No synthetic bracket or expected text is used.
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(full, destination)
        shutil.copyfile(number, number_path)
        l, t, r, b = proposed["box"]
        if not cv2.imwrite(str(original_number_path), reference[t:b, l:r]):
            raise ValueError("编号原图核对图保存失败")
    return proposed, {
        "status": "restored", "method": "original_number_component",
        "original_detection": detection, "component_box": component_box,
        "restored_pixels": int(np.count_nonzero(np.any(restored != clean, axis=2))),
        "verified_text": rows[0].text, "confidence": float(rows[0].confidence),
    }
