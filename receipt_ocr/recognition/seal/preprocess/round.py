"""Stamp orientation: round."""

import numpy as np
from PIL import Image
from receipt_ocr.recognition.seal.preprocess.orientation_angles import _round_stamp_type_texts, choose_round_stamp_angle
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _oriented_type_row_box, _oriented_type_row_exact_box, _oriented_type_row_polygons, _rotation_geometry, rotate_stamp_image


def prepare_round_stamp(source, destination, *, model_variant="v6"):
    """Detect a stamp-type line and make an oriented OCR derivative."""
    from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

    boxes = detect_boxes(source, model_variant=model_variant)
    decision = choose_round_stamp_angle(boxes)
    decision["mode"] = "polygon"
    decision["model_variant"] = model_variant
    decision["detected_boxes"] = boxes
    decision["type_row_texts"] = _round_stamp_type_texts(boxes, decision)
    with Image.open(source) as image:
        source_width, source_height = image.size
    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    decision["type_row_box"] = _oriented_type_row_box(
        boxes, decision, identity, source_width, source_height
    )
    decision["type_row_exact_box"] = _oriented_type_row_exact_box(
        boxes, decision, identity, source_width, source_height
    )
    decision["type_row_polygons"] = _oriented_type_row_polygons(
        boxes, decision, identity
    )
    if not decision.get("applied_rotation"):
        decision["oriented_type_row_box"] = decision.get("type_row_box")
        decision["oriented_type_row_exact_box"] = decision.get("type_row_exact_box")
        decision["oriented_path"] = ""
        decision["status"] = (
            "印章已接近水平，保留原方向"
            if decision.get("angle") is not None
            else decision["status"]
        )
        return None, decision
    oriented = rotate_stamp_image(
        source, destination, decision["applied_rotation"]
    )
    with Image.open(oriented) as image:
        oriented_width, oriented_height = image.size
    with Image.open(source) as image:
        source_width, source_height = image.size
    matrix, _, _ = _rotation_geometry(
        source_width, source_height, decision["applied_rotation"]
    )
    decision["oriented_type_row_box"] = _oriented_type_row_box(
        boxes,
        decision,
        matrix,
        oriented_width,
        oriented_height,
    )
    decision["oriented_type_row_exact_box"] = _oriented_type_row_exact_box(
        boxes,
        decision,
        matrix,
        oriented_width,
        oriented_height,
    )
    decision["oriented_type_row_polygons"] = _oriented_type_row_polygons(
        boxes, decision, matrix
    )
    decision["oriented_path"] = str(oriented)
    decision["status"] = "已按印章文字检测框旋正"
    return oriented, decision
