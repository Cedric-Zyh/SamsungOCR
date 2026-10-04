"""Stamp orientation: doc orientation."""

import math
import cv2
import numpy as np
from PIL import Image
from receipt_ocr.recognition.seal.preprocess.orientation_settings import DOC_ORIENTATION_MIN_CONFIDENCE
from receipt_ocr.recognition.seal.preprocess.orientation_angles import choose_round_stamp_angle
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _oriented_type_row_box, _oriented_type_row_exact_box, _rotation_geometry, _transform_box, rotate_stamp_image
from receipt_ocr.providers.orientation import classify_doc_orientation


def prepare_round_stamp_doc_ori(source, destination):
    """Rotate a round stamp using Paddle's four-way document orientation model."""
    decision = classify_doc_orientation(source)
    angle = decision.get("angle")
    decision.update(
        mode="doc_ori",
        applied_rotation=0.0,
        oriented_path="",
    )
    if angle is None:
        decision["status"] = "doc_ori 未返回有效方向，保留原方向"
        return None, decision
    confidence = float(decision.get("confidence", 0.0) or 0.0)
    if not math.isfinite(confidence) or confidence < DOC_ORIENTATION_MIN_CONFIDENCE:
        decision["status"] = "doc_ori 方向置信度不足 90%，保留原方向"
        return None, decision
    try:
        with Image.open(source) as image:
            source_width, source_height = image.size
    except (OSError, ValueError):
        source_width = source_height = None
    if angle == 0:
        decision["status"] = "doc_ori 判断为 0°，保留原方向"
        return None, decision
    oriented = rotate_stamp_image(source, destination, angle)
    decision["applied_rotation"] = float(angle)
    if not source_width or not source_height:
        decision["oriented_path"] = str(oriented)
        decision["status"] = f"doc_ori 按 {angle}° 粗校正，未确认文字方向"
        return oriented, decision
    with Image.open(oriented) as image:
        oriented_width, oriented_height = image.size
    matrix, _, _ = _rotation_geometry(source_width, source_height, angle)
    oriented_box = None
    # Only an observed, horizontal type row can define a mask. A guessed
    # centre band can erase company lettering on stamps with only ring text.
    try:
        from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

        boxes = detect_boxes(oriented, model_variant="v6")
        row_decision = choose_round_stamp_angle(boxes, minimum_confidence=0.45)
        detected_box = _oriented_type_row_box(
            boxes, row_decision, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            oriented_width, oriented_height,
        )
        if detected_box and abs(float(row_decision.get("angle") or 0.0)) <= 2.0:
            oriented_box = detected_box
    except Exception:
        pass
    if oriented_box:
        decision["oriented_type_row_box"] = oriented_box
        decision["oriented_type_row_exact_box"] = _oriented_type_row_exact_box(
            boxes, row_decision, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            oriented_width, oriented_height,
        )
        decision["type_row_box"] = _transform_box(
            oriented_box, cv2.invertAffineTransform(matrix), source_width, source_height
        )
    decision["oriented_path"] = str(oriented) if oriented is not None else ""

    decision["status"] = f"doc_ori 按 {angle}° 粗校正，未确认文字方向"
    return oriented, decision
