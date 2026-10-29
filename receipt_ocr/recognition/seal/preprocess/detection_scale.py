"""Locate orientation rows at a bounded scale, retaining full-size geometry."""

import math
from pathlib import Path

from PIL import Image
import cv2
import numpy as np
from receipt_ocr.imaging.working_images import open_rgb, cached_transform
from ..ocr.interface import write_temporary_input

from .orientation_angles import choose_round_stamp_angle
from .orientation_settings import FOUR_WAY_ANCHOR_MIN_CONFIDENCE
from .horizontal_rows import split_type_rows
from ..layout_fields import inner_role


def detect_orientation_boxes(source, scratch, *, model_variant):
    from ..ocr.interface import detect_boxes

    with open_rgb(source) as image:
        width, height = image.size
        scale = min(1.0, 512 / max(width, height))
        if scale == 1:
            return detect_boxes(source, model_variant=model_variant)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        preview = Path(scratch) / f"{Path(source).stem}-detection.png"
        rgb = image.convert('RGB')
        scaled = cached_transform(np.asarray(rgb), 'resize', ('pil_rgb_lanczos', size),
                                  lambda: np.asarray(rgb.resize(size, Image.Resampling.LANCZOS)))
        write_temporary_input(preview, cv2.cvtColor(scaled, cv2.COLOR_RGB2BGR))

    boxes = detect_boxes(preview, model_variant=model_variant)
    anchor = choose_round_stamp_angle(
        boxes, minimum_confidence=FOUR_WAY_ANCHOR_MIN_CONFIDENCE,
    )
    department = any(inner_role(box.get('text', '')) == 'department'
                     and float(box.get('confidence') or 0) >= .90 for box in boxes)
    if not anchor.get("anchor_text") and not next(split_type_rows(boxes), None) and not department:
        # Preserve fine-detail detections that genuinely need the larger image.
        return detect_boxes(source, model_variant=model_variant)

    sx, sy = width / size[0], height / size[1]
    mapped = []
    for box in boxes:
        item = dict(box)
        points = [[float(x) * sx, float(y) * sy] for x, y in box["points"]]
        item["points"] = points
        edges = [(points[(i + 1) % len(points)][0] - x,
                  points[(i + 1) % len(points)][1] - y)
                 for i, (x, y) in enumerate(points)]
        dx, dy = max(edges, key=lambda e: e[0] ** 2 + e[1] ** 2)
        item["angle"] = (math.degrees(math.atan2(dy, dx)) + 90) % 180 - 90
        mapped.append(item)
    return mapped
