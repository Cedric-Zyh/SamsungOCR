"""Read a separate short seal number below the observed stamp-type row."""
import math
from pathlib import Path
import re
import tempfile

import cv2
import numpy as np
from PIL import Image

from receipt_ocr.domain.parsing.parsing_seals import normalize_seal_text_strict
from .orientation_angles import _stamp_type_marker_strength


MIN_NUMBER_CONFIDENCE = 0.80


def seal_number(text):
    """Only typography is normalized; never guess a digit from a letter."""
    raw = re.sub(r"\s+", "", str(text or ""))
    if not re.fullmatch(r"[（(]?[0-9０-９]{1,3}[）)]?", raw):
        return ""
    value = normalize_seal_text_strict(raw)
    return value if re.fullmatch(r"[0-9]{1,3}", value) else ""


def _tight_number_box(search, row_height):
    """Locate a compact number-sized group without reading or changing pixels.

    The stripe artwork can join into one large component around the number.
    Keep only separate, similarly tall central glyphs; a number with one or
    both parentheses usually contributes two to five such components. The
    chroma mask is for localization only, never an OCR image replacement.
    """
    rgb = np.asarray(search.convert("RGB")).astype(np.int16)
    ink = (rgb.max(axis=2) - rgb.min(axis=2) >= 16).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    pieces = sorted(
        (tuple(map(int, s)) for s in stats[1:]
         if row_height * .35 <= s[3] <= row_height * 1.1
         and s[2] <= row_height * .7
         and s[4] >= row_height * row_height * .01
         and s[1] <= row_height * .7
         and abs(s[0] + s[2] / 2 - search.width / 2) <= min(search.width * .25, row_height)
         and s[1] + s[3] < search.height - 2),
        key=lambda item: item[0],
    )
    if not 2 <= len(pieces) <= 5:
        return None
    heights = [s[3] for s in pieces]
    baselines = [s[1] + s[3] for s in pieces]
    if (max(heights) > 1.8 * min(heights)
            or max(baselines) - min(baselines) > row_height * .45
            or any(right[0] - (left[0] + left[2]) > row_height * .65
                   for left, right in zip(pieces, pieces[1:]))):
        return None
    left, top = min(s[0] for s in pieces), min(s[1] for s in pieces)
    right, bottom = max(s[0] + s[2] for s in pieces), max(baselines)
    if right - left > search.width * .65:
        return None
    # Only a small sampling margin: the old broad padding reintroduced the
    # adjacent stripes and could change a legible (1) into (7).
    padding = max(2, round(row_height * .03))
    return [max(0, left - padding), max(0, top - padding),
            min(search.width, right + padding), min(search.height, bottom + padding)]


def prepare_number_input(source, destination, focus_box, *, reader, provider):
    """Locate one number on the final upright, color-only image.

    Search only directly below and within the type row, away from the rim.
    First tighten the search to separate central glyphs when possible. A
    detector reading and a later independent line read must agree before
    the number can be joined. Nothing from the requirement or another stamp
    is available here. Keep search/detection coordinates for review.
    """
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        left, top, right, bottom = [float(value) for value in focus_box]
        row_height, row_width = bottom - top, right - left
        if row_height < 8 or row_width < row_height * 2:
            return {"status": "unavailable"}
        search_box = (
            max(0, round(left)), max(0, round(bottom)),
            min(width, round(right)), min(height, round(bottom + row_height * 1.5)),
        )
        if search_box[2] <= search_box[0] or search_box[3] <= search_box[1]:
            return {"status": "unavailable"}
        search = image.crop(search_box)
        tight_box = _tight_number_box(search, row_height)
        detector_image = search.crop(tight_box) if tight_box else search
        with tempfile.TemporaryDirectory(prefix="receipt-seal-number-") as scratch:
            search_path = Path(scratch) / "number-search.png"
            detector_image.save(search_path)
            boxes = reader.detect_boxes(search_path, provider=provider)
        candidates = []
        for box in boxes or []:
            text = str(box.get("text") or "").strip()
            confidence = float(box.get("confidence") or 0.0)
            angle = float(box.get("angle") or 0.0)
            points = box.get("points") or []
            if (not seal_number(text) or not math.isfinite(confidence)
                    or confidence < MIN_NUMBER_CONFIDENCE
                    or not math.isfinite(angle) or abs(angle) > 15 or len(points) < 4):
                continue
            # The detector sees the tight crop, but all spatial gates and
            # persisted coordinates stay in the original upright search frame.
            offset_x, offset_y = tight_box[:2] if tight_box else (0, 0)
            xs = [p[0] + offset_x for p in points]
            ys = [p[1] + offset_y for p in points]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            # Small, centred text immediately under the type row is eligible;
            # a rim serial, a date, or a second row farther down is not.
            if (not all(math.isfinite(v) for v in (x1, y1, x2, y2))
                    or not row_height * 0.3 <= y2 - y1 <= row_height * 1.5
                    or not 0 < x2 - x1 <= row_width * 0.65
                    or abs((x1 + x2) / 2 - search.width / 2) > row_width * 0.25
                    or y1 > row_height * 0.75):
                continue
            pad_x, pad_y = max(4, row_height * 0.14), max(2, row_height * 0.06)
            crop_box = [
                search_box[0] + max(0, math.floor(x1 - pad_x)),
                search_box[1] + max(0, math.floor(y1 - pad_y)),
                search_box[0] + min(search.width, math.ceil(x2 + pad_x)),
                search_box[1] + min(search.height, math.ceil(y2 + pad_y)),
            ]
            if tight_box:
                crop_box = [search_box[0] + tight_box[0], search_box[1] + tight_box[1],
                            search_box[0] + tight_box[2], search_box[1] + tight_box[3]]
            candidates.append({"text": text, "confidence": confidence, "box": crop_box})
        evidence = {"search_box": list(search_box), "candidates": candidates}
        if tight_box:
            evidence.update(method="tight_ink_components", tight_box=[
                search_box[0] + tight_box[0], search_box[1] + tight_box[1],
                search_box[0] + tight_box[2], search_box[1] + tight_box[3],
            ])
        if len(candidates) != 1:
            return {**evidence, "status": "ambiguous" if candidates else "not_detected"}
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.crop(candidates[0]["box"]).save(destination)
    return {**evidence, **candidates[0], "status": "detected"}


def merge_number_read(artifact):
    """Join only corroborated reads from this artifact; retain every raw read."""
    detection = artifact.get("stamp_number_detection") or {}
    if detection.get("status") != "detected":
        return
    center = [row for row in artifact.get("reads", []) if row.get("channel") == "center" and row.get("text")]
    numbers = [row for row in artifact.get("reads", []) if row.get("channel") == "number" and row.get("text")]
    artifact["stamp_number_text"] = " | ".join(row["text"] for row in numbers)
    artifact["stamp_number_status"] = "unverified"
    if len(center) != 1 or len(numbers) != 1:
        return
    type_row, number_row = center[0], numbers[0]
    expected_number = seal_number(detection.get("text"))
    actual_number = seal_number(number_row["text"])
    confidences = [float(row.get("confidence") or 0) for row in (type_row, number_row, detection)]
    if (not expected_number or actual_number != expected_number
            or any(not math.isfinite(value) or value < MIN_NUMBER_CONFIDENCE for value in confidences)
            or type_row.get("provider") != number_row.get("provider")
            or type_row.get("region_id") != artifact.get("region_id")
            or number_row.get("region_id") != artifact.get("region_id")
            or _stamp_type_marker_strength(type_row["text"]) < 2):
        return
    # If the type row already contains a number, do not duplicate or silently
    # replace it with a conflicting separate number.
    trailing = re.search(r"[0-9]{1,3}$", normalize_seal_text_strict(type_row["text"]))
    if trailing:
        artifact["stamp_number_status"] = "already_in_type_row" if trailing[0] == actual_number else "conflict"
        return
    combined = type_row["text"] + number_row["text"]
    artifact.update(
        round_type_row_text=type_row["text"],
        round_type_band_text=combined,
        round_type_band_texts=[combined],
        stamp_number_status="merged",
    )
