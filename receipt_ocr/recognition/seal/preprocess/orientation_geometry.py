"""Stamp orientation: geometry."""

from pathlib import Path
import math
import cv2
import numpy as np


def _rotation_geometry(width, height, angle):
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), float(angle), 1.0)
    new_width = int(math.ceil(height * abs(matrix[0, 1]) + width * abs(matrix[0, 0])))
    new_height = int(math.ceil(height * abs(matrix[0, 0]) + width * abs(matrix[0, 1])))
    matrix[0, 2] += (new_width - width) / 2.0
    matrix[1, 2] += (new_height - height) / 2.0
    return matrix, new_width, new_height

def _transform_points(points, matrix):
    if not points:
        return []
    transformed = cv2.transform(
        np.asarray([points], dtype=np.float32), matrix.astype(np.float32)
    )[0]
    return [[round(float(point[0]), 2), round(float(point[1]), 2)] for point in transformed]

def _transform_box(box, matrix, width, height):
    if not box or len(box) != 4:
        return None
    left, top, right, bottom = [float(value) for value in box]
    points = _transform_points(
        [[left, top], [right, top], [right, bottom], [left, bottom]], matrix
    )
    if not points:
        return None
    return [
        max(0.0, min(float(width), min(point[0] for point in points))),
        max(0.0, min(float(height), min(point[1] for point in points))),
        max(0.0, min(float(width), max(point[0] for point in points))),
        max(0.0, min(float(height), max(point[1] for point in points))),
    ]

def _oriented_type_row_polygons(boxes, decision, matrix):
    """Return the detector polygons that make up the horizontal type row."""
    anchor_text = str(decision.get("anchor_text", ""))
    anchor_points = decision.get("anchor_points") or []
    anchor = _transform_points(anchor_points, matrix)
    if not anchor:
        return []
    ax = sum(point[0] for point in anchor) / len(anchor)
    ay = sum(point[1] for point in anchor) / len(anchor)
    anchor_height = max(point[1] for point in anchor) - min(point[1] for point in anchor)
    selected = [anchor]
    for box in boxes or []:
        text = str(box.get("text", "")).strip()
        if text == anchor_text and box.get("points") == anchor_points:
            continue
        points = _transform_points(box.get("points") or [], matrix)
        if not points:
            continue
        cx = sum(point[0] for point in points) / len(points)
        cy = sum(point[1] for point in points) / len(points)
        box_height = max(point[1] for point in points) - min(point[1] for point in points)
        try:
            angle_delta = abs(float(box.get("angle", decision.get("angle", 0))) - float(decision.get("angle", 0)))
            angle_delta = min(angle_delta, abs(180 - angle_delta))
        except (TypeError, ValueError):
            continue
        same_row = angle_delta <= 12 and abs(cy - ay) <= max(anchor_height, box_height, 12) * 1.8
        related = any(token in text for token in ("收货", "专用", "用章", "货专"))
        if same_row and related:
            selected.append(points)
    return selected

def _oriented_type_row_box(boxes, decision, matrix, width, height):
    """Find the horizontal stamp-type row after the rotation.

    The old implementation used a fixed percentage of the circular crop.  A
    crop can have a different border, aspect ratio, or expanded rotation
    canvas, so that slice may contain only the star.  Anchor the row to the
    detected ``用章/专用章`` polygon and include adjacent text boxes on the same
    baseline (for example ``收货专`` + ``用章``).
    """
    selected = _oriented_type_row_polygons(boxes, decision, matrix)
    if not selected:
        return None
    all_points = [point for points in selected for point in points]
    left = min(point[0] for point in all_points)
    right = max(point[0] for point in all_points)
    top = min(point[1] for point in all_points)
    bottom = max(point[1] for point in all_points)
    row_height = max(1.0, bottom - top)
    padding_x = max(12.0, row_height * 0.45)
    padding_y = max(12.0, row_height * 0.35)
    return [
        max(0.0, left - padding_x),
        max(0.0, top - padding_y),
        min(float(width), right + padding_x),
        min(float(height), bottom + padding_y),
    ]

def _oriented_type_row_exact_box(boxes, decision, matrix, width, height):
    """Return the tight OCR detection box without any padding."""
    polygons = _oriented_type_row_polygons(boxes, decision, matrix)
    if not polygons:
        return None
    points = [point for polygon in polygons for point in polygon]
    return [
        max(0.0, min(float(width), min(point[0] for point in points))),
        max(0.0, min(float(height), min(point[1] for point in points))),
        max(0.0, min(float(width), max(point[0] for point in points))),
        max(0.0, min(float(height), max(point[1] for point in points))),
    ]

def _oriented_type_anchor_box(decision, matrix, width, height):
    """Return a focused box around the stamp-type polygon after rotation.

    The document-orientation pass can leave several curved ring fragments on
    the same baseline as the type suffix.  Unioning those fragments makes the
    next crop include the star and most of the circle, so the OCR line sees a
    partial character instead of the complete stamp-type line.  The combined route
    has already selected the best type polygon; keep that polygon as the
    anchor and add only enough side/top padding for a clipped leading ``三``.
    """
    points = _transform_points(decision.get("anchor_points") or [], matrix)
    if not points:
        return None
    left = min(point[0] for point in points)
    right = max(point[0] for point in points)
    top = min(point[1] for point in points)
    bottom = max(point[1] for point in points)
    text_height = max(1.0, bottom - top)
    # The detector occasionally drops the first glyph of the type row.  Keep
    # a near half-character margin on each side so that glyph remains visible,
    # while a narrow vertical margin excludes the star and the lower ring.
    padding_x = max(16.0, text_height * 0.45)
    padding_y = max(12.0, text_height * 0.12)
    return [
        max(0.0, left - padding_x),
        max(0.0, top - padding_y),
        min(float(width), right + padding_x),
        min(float(height), bottom + padding_y),
    ]

def rotate_stamp_image(source, destination, angle):
    """Rotate a color-isolated stamp with a white expanded canvas."""
    source, destination = Path(source), Path(destination)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"无法读取印章方向校正图：{source}")
    height, width = image.shape[:2]
    matrix, new_width, new_height = _rotation_geometry(width, height, angle)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(destination), rotated)
    if not ok:
        raise ValueError(f"印章方向校正图写入失败：{destination}")
    return destination

def _ellipse_axis_angle(source):
    """Estimate the remaining oval tilt without another OCR inference."""
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    red = (((hue <= 18) | (hue >= 162)) & (saturation >= 20) & (value >= 35))
    blue = ((hue >= 82) & (hue <= 140) & (saturation >= 20) & (value >= 35))
    b, g, r = cv2.split(image)
    chromatic = (r.astype(np.int16) - np.maximum(g, b).astype(np.int16)) >= 4
    mask = np.where(red | blue | chromatic, 255, 0).astype(np.uint8)
    height, width = mask.shape[:2]
    kernel_size = max(5, min(31, int(round(min(height, width) * 0.015))))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if len(contour) < 5 or cv2.contourArea(contour) < height * width * 0.03:
        return None
    (_, _), (axis_a, axis_b), ellipse_angle = cv2.fitEllipse(contour)
    if min(axis_a, axis_b) <= 0 or max(axis_a, axis_b) / min(axis_a, axis_b) < 1.15:
        return None
    # OpenCV reports the fitted ellipse angle in image coordinates.  Convert
    # the long-axis direction to [-90, 90), which is also the sign convention
    # used by the text-polygon rotation path above.
    major = float(ellipse_angle if axis_a >= axis_b else ellipse_angle + 90.0)
    while major >= 90.0:
        major -= 180.0
    while major < -90.0:
        major += 180.0
    return major

def _refine_ellipse_axis(source, destination):
    angle = _ellipse_axis_angle(source)
    if angle is None or abs(angle) <= 2.0:
        return Path(source), 0.0
    return rotate_stamp_image(source, destination, angle), angle

def _map_box_to_source(box, candidate, coarse_angle, source_size):
    """Map a box on the final candidate image back onto the original crop."""
    if not box:
        return None
    mapped = box
    if candidate["fine_rotation"]:
        mapped = _transform_box(
            mapped,
            cv2.invertAffineTransform(candidate["fine_matrix"]),
            candidate["candidate_width"],
            candidate["candidate_height"],
        )
    if mapped and coarse_angle:
        source_width, source_height = source_size
        coarse_matrix, _, _ = _rotation_geometry(
            source_width, source_height, coarse_angle
        )
        mapped = _transform_box(
            mapped, cv2.invertAffineTransform(coarse_matrix), source_width, source_height
        )
    return mapped
