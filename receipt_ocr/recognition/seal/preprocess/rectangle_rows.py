"""Keep the first rectangular-stamp text localization throughout recognition."""
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image, _write_stage_image
from .orientation_geometry import _rotation_geometry


def prepare_fixed_rectangle_rows(color, oriented, orientation, directory, index):
    """Crop observed boxes once; rotation maps boxes instead of redetecting.

    With no rotation the first detector already recognized these pixels, so
    its text is retained. Rotated rows need line recognition only. Crops are
    also saved for review, but are not claimed as OCR inputs for reused reads.
    """
    original = _read_image(color)
    angle = float(orientation.get("applied_rotation") or 0)
    rotated = abs(angle) >= .01 or bool(orientation.get('force_row_read'))
    image = _read_image(oriented) if rotated else original
    height, width = image.shape[:2]
    matrix = None
    if rotated:
        pad = int(orientation.get("padding") or 0)
        matrix, _, _ = _rotation_geometry(
            original.shape[1] + 2 * pad, original.shape[0] + 2 * pad, angle,
        )
        if orientation.get('rotation_matrix') is not None:
            matrix = np.asarray(orientation['rotation_matrix'], np.float64)
    rows = []
    for detection_index, box in enumerate(orientation.get("detected_boxes", [])):
        if float(box.get("confidence") or 0) < .75 or not str(box.get("text") or "").strip():
            continue
        points = np.asarray(box.get("points", []), dtype=np.float32)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        if matrix is not None:
            points = cv2.transform((points + pad)[None], matrix)[0]
        low, high = points.min(axis=0), points.max(axis=0)
        if np.any(high <= low):
            continue
        rows.append({
            "detection_index": detection_index,
            "points": points.tolist(),
            "text": str(box["text"]).strip(),
            "confidence": float(box["confidence"]),
            "x": float(low[0] / width), "y": float(low[1] / height),
            "width": float((high[0] - low[0]) / width),
            "height": float((high[1] - low[1]) / height),
        })
    # Sort in the same final coordinate frame used for the stamp preview.
    rows.sort(key=lambda row: (row["y"] + row["height"] / 2, row["x"]))
    for row_index, row in enumerate(rows):
        points = np.asarray(row["points"])
        low = np.floor(points.min(axis=0)).astype(int)
        high = np.ceil(points.max(axis=0)).astype(int)
        left, top = max(0, low[0] - 2), max(0, low[1] - 2)
        right, bottom = min(width, high[0] + 3), min(height, high[1] + 3)
        if right <= left or bottom <= top:
            raise ValueError("矩形文字框超出图像范围")
        crop = cv2.copyMakeBorder(
            image[top:bottom, left:right], 20, 20, 20, 20,
            cv2.BORDER_CONSTANT, value=(255, 255, 255),
        )
        path = Path(directory) / f"seal-{index}-rectangular-row-{row_index}.png"
        _write_stage_image(path, crop, ".png")
        row.update(path=str(path), crop_box=[int(v) for v in (left, top, right, bottom)])
    return rows, rotated
