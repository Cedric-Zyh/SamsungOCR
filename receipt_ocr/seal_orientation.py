"""Stamp orientation helpers used before local seal OCR.

Official model: https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/
module_usage/textline_orientation_classification.html
Only unanimous, high-confidence 0/180 line predictions are actionable.
No requirement/template text is consulted to choose an angle.
"""

from pathlib import Path
import os
import threading
import math
import re
import tempfile

import cv2
import numpy as np
from PIL import Image

from .image_processing import save_isolated_seal, save_region_crop

MODEL_NAME = "PP-LCNet_x0_25_textline_ori"
DOC_ORIENTATION_MODEL_NAME = "PP-LCNet_x1_0_doc_ori"
DOC_ORIENTATION_PROVIDER = "paddle_doc_ori"
DEFAULT_SEAL_ORIENTATION_MODE = "polygon"
SEAL_ORIENTATION_MODES = (
    {
        "id": "none",
        "label": "不处理",
        "description": "保留印章原方向，直接进行本地印章 OCR",
    },
    {
        "id": "polygon",
        "label": "文本框角度估计",
        "description": "用“用章/专用章”检测框的四点坐标估计角度",
    },
    {
        "id": "doc_ori",
        "label": "文档方向分类（doc_ori）",
        "description": "使用 PP-LCNet_x1_0_doc_ori，按 0/90/180/270° 旋正",
    },
)
_SEAL_ORIENTATION_MODE_IDS = {item["id"] for item in SEAL_ORIENTATION_MODES}


def resolve_seal_orientation_mode(value: str | None) -> str:
    selected = str(value or DEFAULT_SEAL_ORIENTATION_MODE).strip().lower()
    if selected not in _SEAL_ORIENTATION_MODE_IDS:
        raise ValueError(f"不支持的印章方向处理方式：{selected}")
    return selected


def seal_orientation_mode_label(value: str | None) -> str:
    selected = resolve_seal_orientation_mode(value)
    return next(item["label"] for item in SEAL_ORIENTATION_MODES if item["id"] == selected)


# Rectangular stamp text can be faint or partially covered.  Keep the
# unanimous-direction guard, but allow a weaker individual line to contribute
# when the direction model still agrees across all detected lines.
MIN_CONFIDENCE = 0.70
_classifier = None
_doc_classifier = None
_lock = threading.Lock()


ROUND_STAMP_ANCHOR_MIN_CONFIDENCE = 0.70


def classify_doc_orientation(image_path):
    """Return Paddle's four-way document orientation prediction for one crop."""
    global _doc_classifier
    from .paddle_ocr import paddle_model_home, _prediction_slot
    from .recognition_scope import provider_allowed

    if not provider_allowed(DOC_ORIENTATION_PROVIDER):
        return {"angle": None, "confidence": 0.0, "model": DOC_ORIENTATION_MODEL_NAME}
    with _prediction_slot(), _lock:
        if _doc_classifier is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_model_home()))
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import DocImgOrientationClassification

            # Use the same native Paddle weights as the verified classifier;
            # the page OCR engine may request a separate ONNX model download.
            _doc_classifier = DocImgOrientationClassification(
                model_name=DOC_ORIENTATION_MODEL_NAME,
                device="cpu",
            )
        predictions = list(_doc_classifier.predict(input=str(image_path), batch_size=1))
    if not predictions:
        return {"angle": None, "confidence": 0.0, "model": DOC_ORIENTATION_MODEL_NAME}
    prediction = predictions[0]
    labels = prediction.get("label_names", [])
    scores = prediction.get("scores", [])
    try:
        angle = int(str(labels[0]))
    except (IndexError, TypeError, ValueError):
        angle = None
    try:
        confidence = float(scores[0])
    except (IndexError, TypeError, ValueError):
        confidence = 0.0
    if angle not in (0, 90, 180, 270):
        angle = None
    return {
        "model": DOC_ORIENTATION_MODEL_NAME,
        "angle": angle,
        "confidence": round(confidence, 4),
    }


def choose_round_stamp_angle(boxes, *, minimum_confidence=ROUND_STAMP_ANCHOR_MIN_CONFIDENCE):
    """Choose a round-stamp rotation from a detected ``用章`` text polygon.

    The outer circle has no unique direction.  The fixed stamp-type suffix is
    the direction anchor instead.  Prefer the longest recognized suffix and
    accept partial readings such as ``货专用章``; never manufacture the missing
    prefix from the requested seal text.
    """
    candidates = []
    for box in boxes or []:
        text = str(box.get("text", "")).strip()
        confidence = float(box.get("confidence", 0.0) or 0.0)
        if confidence < minimum_confidence:
            continue
        # Round and oval receiving stamps often print the shorter centre row
        # ``收货章`` rather than ``收货专用章``.  It is still a valid direction
        # anchor; the row's text polygon is more reliable than the ellipse's
        # long axis because it also resolves the 180-degree ambiguity.
        is_stamp_type = (
            "用章" in text
            or "专用" in text
            or ("收货" in text and "章" in text)
        )
        if not is_stamp_type:
            continue
        try:
            angle = float(box.get("angle"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(angle):
            continue
        candidates.append((
            (1 if "用章" in text else 0, len(text), confidence),
            {
                "text": text,
                "confidence": confidence,
                "angle": angle,
                "points": box.get("points") or [],
            },
        ))
    if not candidates:
        return {
            "status": "未找到印章类型方向锚点",
            "angle": None,
            "applied_rotation": 0.0,
            "anchor_text": "",
            "confidence": 0.0,
        }
    _, chosen = max(candidates, key=lambda item: item[0])
    angle = chosen["angle"]
    # A nearly horizontal line is already normalized.  Keep the measured value
    # in the audit record, but avoid a needless interpolation pass.
    applied = 0.0 if abs(angle) < 2.0 else angle
    return {
        "status": "找到印章类型方向锚点",
        "angle": round(angle, 3),
        "applied_rotation": round(applied, 3),
        "anchor_text": chosen["text"],
        "confidence": round(chosen["confidence"], 4),
        "anchor_points": chosen.get("points", []),
    }


def _round_stamp_type_texts(boxes, decision):
    """Reuse detected type-row text, including a second numeric suffix line."""
    anchor_text = str(decision.get("anchor_text", "")).strip()
    if not anchor_text:
        return []
    try:
        anchor_angle = float(decision.get("angle", 0.0) or 0.0)
    except (TypeError, ValueError):
        anchor_angle = 0.0
    marker_pattern = re.compile(r"^[（(]?\s*\d{1,3}\s*[）)]?$")
    values = []
    for box in boxes or ():
        text = str(box.get("text", "")).strip()
        if text != anchor_text and not marker_pattern.fullmatch(text):
            continue
        try:
            angle = float(box.get("angle", anchor_angle))
        except (TypeError, ValueError):
            angle = anchor_angle
        delta = abs(angle - anchor_angle)
        delta = min(delta, abs(180.0 - delta))
        if text != anchor_text and delta > 15.0:
            continue
        if text not in values:
            values.append(text)
    if anchor_text in values:
        values.remove(anchor_text)
    return [anchor_text, *values]


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


def _oriented_type_row_box(boxes, decision, matrix, width, height):
    """Find the horizontal stamp-type row after the rotation.

    The old implementation used a fixed percentage of the circular crop.  A
    crop can have a different border, aspect ratio, or expanded rotation
    canvas, so that slice may contain only the star.  Anchor the row to the
    detected ``用章/专用章`` polygon and include adjacent text boxes on the same
    baseline (for example ``收货专`` + ``用章``).
    """
    anchor_text = str(decision.get("anchor_text", ""))
    anchor_points = decision.get("anchor_points") or []
    anchor = _transform_points(anchor_points, matrix)
    if not anchor:
        return None
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


def prepare_round_stamp(source, destination, *, model_variant="mobile"):
    """Detect a stamp-type line and make an oriented OCR derivative."""
    from .paddle_ocr import detect_text_boxes

    boxes = detect_text_boxes(source, model_variant=model_variant)
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
    if not decision.get("applied_rotation"):
        decision["oriented_type_row_box"] = decision.get("type_row_box")
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
    decision["oriented_path"] = str(oriented)
    decision["status"] = "已按印章文字检测框旋正"
    return oriented, decision


def prepare_ellipse_stamp(source, destination, *, model_variant="mobile"):
    """Orient an oval stamp with its centre line, including quarter-turns.

    Ellipse geometry can estimate tilt but cannot tell which end is upright.
    Reuse the normal text-polygon route first; when the detector has no
    trustworthy anchor, compare the centre-line reading at all four right
    angle orientations.  This also handles an oval whose long axis is
    vertical, where a horizontal centre crop otherwise sees only one glyph.
    """
    oriented, decision = prepare_round_stamp(
        source, destination, model_variant=model_variant
    )
    decision["shape_route"] = "ellipse"
    if oriented is not None or decision.get("anchor_text"):
        # The polygon route may already have selected a quarter-turn, but an
        # oval captured with perspective can still leave the centre row
        # slanted. Always refine that successful route before returning; the
        # fallback below is only responsible for choosing a missing direction.
        base = oriented or Path(source)
        try:
            from .paddle_ocr import detect_text_boxes

            fine_boxes = detect_text_boxes(base, model_variant=model_variant)
            fine_decision = choose_round_stamp_angle(
                fine_boxes, minimum_confidence=0.45
            )
            fine_rotation = float(fine_decision.get("angle") or 0.0)
            if abs(fine_rotation) > 2.0:
                oriented = rotate_stamp_image(base, destination, fine_rotation)
                decision["fine_rotation"] = round(fine_rotation, 3)
                decision["status"] = (
                    f"{decision.get('status', '已按印章文字检测框旋正')}，"
                    f"再微调 {fine_rotation:.1f}°"
                )
                decision["oriented_path"] = str(oriented)
            geometry_source = oriented or base
            geometry_path, geometry_rotation = _refine_ellipse_axis(
                geometry_source, destination
            )
            if abs(geometry_rotation) > 2.0:
                oriented = geometry_path
                decision["geometry_rotation"] = round(geometry_rotation, 3)
                decision["oriented_path"] = str(oriented)
                decision["status"] = (
                    f"{decision.get('status', '已按印章文字方向旋正')}，"
                    f"按椭圆长轴再校正 {geometry_rotation:.1f}°"
                )
        except Exception:
            pass
        return oriented, decision

    from .paddle_ocr import recognize_line

    def _score(rows):
        values = [row for row in rows if getattr(row, "text", "")]
        if not values:
            return 0.0, ""
        text = "".join(str(row.text) for row in values)
        confidence = sum(float(getattr(row, "confidence", 0.0) or 0.0)
                         for row in values) / len(values)
        # A direction decision must come from the centre stamp-type row, not
        # a company fragment on the ellipse ring.  If no type marker is read,
        # return no evidence and leave the crop in its current orientation.
        is_type_text = (
            ("收货" in text and "章" in text)
            or ("专用" in text and "章" in text)
            or "代码" in text
            or bool(re.fullmatch(r"[（(]?\s*\d{1,3}\s*[）)]?", text))
        )
        if not is_type_text:
            return 0.0, ""
        marker_bonus = 0.35 if ("收货" in text and "章" in text) else 0.0
        marker_bonus += 0.20 if ("专用" in text and "章" in text) else 0.0
        return confidence + marker_bonus, text

    try:
        with Image.open(source) as image:
            rgb = image.convert("RGB")
        with tempfile.TemporaryDirectory(prefix="receipt-ellipse-ori-") as tmp:
            candidate_rows = {}
            for angle in (0, 90, 180, 270):
                candidate_path = Path(tmp) / f"center-{angle}.png"
                rotated = rgb.rotate(angle, expand=True, fillcolor="white")
                width, height = rotated.size
                left, right = round(width * 0.04), round(width * 0.96)
                # Crop after rotating the full stamp. Cropping before a
                # quarter-turn loses the top and bottom glyphs of a vertical
                # ``收货章`` row, leaving only one character for OCR.
                top, bottom = round(height * 0.38), round(height * 0.64)
                rotated.crop((left, top, right, bottom)).save(candidate_path)
                candidate_rows[angle] = recognize_line(
                    candidate_path, model_variant=model_variant
                )
        scored = {
            angle: _score(rows) for angle, rows in candidate_rows.items()
        }
        best_angle = max(
            scored,
            key=lambda angle: (scored[angle][0], angle in (0, 180), -angle),
        )
        best_score, best_text = scored[best_angle]
        decision["ellipse_orientation_candidates"] = {
            str(angle): {
                "text": text,
                "score": round(score, 4),
            }
            for angle, (score, text) in scored.items()
        }
        if best_score < 0.70:
            decision["status"] = "椭圆中心行未确认方向，保留原方向"
            decision["applied_rotation"] = 0.0
            return None, decision
        oriented = (
            rotate_stamp_image(source, destination, float(best_angle))
            if best_angle
            else Path(source)
        )
        fine_rotation = 0.0
        # A right-angle choice fixes a vertical oval, but camera perspective
        # can leave the centre row tilted by another few degrees. Detect the
        # now-readable type row once more and use its polygon angle for a
        # small corrective rotation.
        try:
            from .paddle_ocr import detect_text_boxes

            fine_boxes = detect_text_boxes(oriented, model_variant=model_variant)
            fine_decision = choose_round_stamp_angle(
                fine_boxes, minimum_confidence=0.45
            )
            fine_rotation = float(fine_decision.get("angle") or 0.0)
            if abs(fine_rotation) > 2.0:
                oriented = rotate_stamp_image(oriented, destination, fine_rotation)
        except Exception:
            fine_rotation = 0.0
        try:
            geometry_path, geometry_rotation = _refine_ellipse_axis(
                oriented, destination
            )
            if abs(geometry_rotation) > 2.0:
                oriented = geometry_path
                decision["geometry_rotation"] = round(geometry_rotation, 3)
                decision["status"] = (
                    f"{decision.get('status', '按椭圆中心单行旋正')}，"
                    f"按椭圆长轴再校正 {geometry_rotation:.1f}°"
                )
        except Exception:
            geometry_rotation = 0.0
        if (
            best_angle == 0
            and abs(fine_rotation) <= 2.0
            and abs(geometry_rotation) <= 2.0
        ):
            decision.update(
                angle=0.0,
                applied_rotation=0.0,
                anchor_text=best_text,
                confidence=round(best_score, 4),
                status="椭圆中心单行已接近水平，保留原方向",
            )
            return None, decision
        decision.update(
            angle=float(best_angle),
            applied_rotation=float(best_angle),
            anchor_text=best_text,
            confidence=round(best_score, 4),
            fine_rotation=round(fine_rotation, 3),
            geometry_rotation=round(geometry_rotation, 3),
            oriented_path=str(oriented),
            status=(
                f"按椭圆中心单行识别结果旋转 {best_angle}°"
                + (f"，再微调 {fine_rotation:.1f}°" if abs(fine_rotation) > 2 else "")
            ),
        )
        return oriented, decision
    except Exception as exc:
        decision["ellipse_orientation_error"] = str(exc)
        decision["status"] = "椭圆中心行方向比较失败，保留原方向"
        decision["applied_rotation"] = 0.0
        return None, decision


def prepare_round_stamp_doc_ori(source, destination):
    """Rotate a round stamp using Paddle's four-way document orientation model."""
    decision = classify_doc_orientation(source)
    angle = decision.get("angle")
    decision.update(
        mode="doc_ori",
        applied_rotation=float(angle or 0),
        oriented_path="",
    )
    if angle is None:
        decision["status"] = "doc_ori 未返回有效方向，保留原方向"
        return None, decision
    try:
        with Image.open(source) as image:
            source_width, source_height = image.size
    except (OSError, ValueError):
        source_width = source_height = None
    if angle == 0:
        if source_width and source_height:
            decision["oriented_type_row_box"] = [
                round(source_width * 0.08), round(source_height * 0.42),
                round(source_width * 0.92), round(source_height * 0.62),
            ]
            decision["type_row_box"] = decision["oriented_type_row_box"]
        decision["status"] = "doc_ori 判断为 0°，保留原方向"
        return None, decision
    oriented = rotate_stamp_image(source, destination, angle)
    if not source_width or not source_height:
        decision["oriented_path"] = str(oriented)
        decision["status"] = f"doc_ori 判断为 {angle}°，已旋正"
        return oriented, decision
    with Image.open(oriented) as image:
        oriented_width, oriented_height = image.size
    matrix, _, _ = _rotation_geometry(source_width, source_height, angle)
    oriented_box = [
        round(oriented_width * 0.08), round(oriented_height * 0.42),
        round(oriented_width * 0.92), round(oriented_height * 0.62),
    ]
    # If the regular detector is available, tighten the fixed center band to
    # the actual horizontal stamp-type text.  This is only for the mask; the
    # doc_ori result remains the sole source of the rotation angle.
    try:
        from .paddle_ocr import detect_text_boxes

        boxes = detect_text_boxes(oriented, model_variant="mobile")
        row_decision = choose_round_stamp_angle(boxes, minimum_confidence=0.45)
        detected_box = _oriented_type_row_box(
            boxes, row_decision, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            oriented_width, oriented_height,
        )
        if detected_box:
            oriented_box = detected_box
    except Exception:
        pass
    decision["oriented_type_row_box"] = oriented_box
    decision["type_row_box"] = _transform_box(
        oriented_box, cv2.invertAffineTransform(matrix), source_width, source_height
    )
    decision["oriented_path"] = str(oriented)
    decision["status"] = f"doc_ori 判断为 {angle}°，已旋正"
    return oriented, decision


def classify_lines(lines):
    """Load the small orientation model once; images never leave the machine."""
    global _classifier
    from .paddle_ocr import paddle_model_home, _prediction_slot, paddle_engine

    with _prediction_slot(), _lock:
        if _classifier is None:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_model_home()))
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextLineOrientationClassification

            engine = paddle_engine()
            _classifier = TextLineOrientationClassification(
                model_name=MODEL_NAME, device="cpu",
                **({"engine": engine} if engine else {}),
            )
        return list(_classifier.predict(input=lines, batch_size=1))


def text_lines(image_path):
    """Remove the border and split horizontal ink bands, not fixed thirds."""
    with Image.open(image_path) as image:
        gray = np.array(image.convert("L"))
    height, width = gray.shape
    if min(height, width) < 12:
        return []
    mask = (gray < 180).astype(np.uint8) * 255
    # Rectangular frames are long straight lines; never classify those as text.
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, max(12, width // 3)), np.uint8))
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(10, height // 2), 1), np.uint8))
    mask = cv2.subtract(mask, cv2.bitwise_or(horizontal, vertical))
    inset_x, inset_y = max(2, round(width * .04)), max(2, round(height * .06))
    mask[:, :inset_x] = mask[:, -inset_x:] = 0
    mask[:inset_y] = mask[-inset_y:] = 0
    active = (np.count_nonzero(mask, axis=1) >= max(3, width * .018)).astype(np.uint8)
    active = cv2.morphologyEx(active[:, None], cv2.MORPH_CLOSE, np.ones((3, 1), np.uint8))[:, 0]
    edges = np.diff(np.r_[0, active, 0].astype(int))
    lines = []
    for top, bottom in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        if bottom - top < max(6, height * .08):
            continue
        xs = np.where(np.any(mask[top:bottom] > 0, axis=0))[0]
        if not len(xs) or xs[-1] - xs[0] < 2 * (bottom - top):
            continue
        crop = gray[max(0, top - 2):min(height, bottom + 2), max(0, xs[0] - 2):min(width, xs[-1] + 3)]
        lines.append(cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))
    return lines


def decide_orientation(predictions):
    readings = []
    for prediction in predictions:
        labels, scores = prediction.get("label_names", []), prediction.get("scores", [])
        label = str(labels[0]) if len(labels) else ""
        angle = {"0_degree": 0, "180_degree": 180}.get(label)
        score = float(scores[0]) if len(scores) else 0.0
        readings.append({"angle": angle, "confidence": score if np.isfinite(score) else 0.0})
    decisive = bool(readings) and all(
        row["angle"] in (0, 180) and row["confidence"] >= MIN_CONFIDENCE for row in readings
    ) and len({row["angle"] for row in readings}) == 1
    return {
        "model": MODEL_NAME, "angle": readings[0]["angle"] if decisive else None,
        "confidence": min((row["confidence"] for row in readings), default=0.0),
        "line_predictions": readings, "applied_rotation": 0,
        "status": "方向已确认" if decisive else "方向不确定，保留原方向",
    }


def prepare_rectangles(source, regions, directory):
    """Make a temporary page copy, rotating only confirmed rectangle boxes.

    Keeping page coordinates unchanged means every later crop/audit reads the
    same corrected pixels. The original upload and round stamps are untouched.
    """
    decisions = {}
    if not any(region.qingtong_cls == "rectangle" for region in regions):
        return source, decisions
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        page = image.convert("RGB")
    for index, region in enumerate(regions):
        if region.qingtong_cls != "rectangle":
            continue
        original = directory / f"seal-{index}-before-orientation.png"
        isolated = directory / f"seal-{index}-orientation-input.png"
        save_region_crop(source, original, region)
        save_isolated_seal(source, isolated, region)
        try:
            lines = text_lines(isolated)
            decision = decide_orientation(classify_lines(lines) if lines else [])
        except Exception as exc:
            decision = decide_orientation([])
            decision.update(status="方向模型不可用，保留原方向", error=str(exc))
        decision["original_path"] = str(original)
        if decision["angle"] == 180:
            box = (
                max(0, int(region.x * page.width)), max(0, int(region.y * page.height)),
                min(page.width, int((region.x + region.width) * page.width)),
                min(page.height, int((region.y + region.height) * page.height)),
            )
            # Overlapping stamp boxes cannot safely be corrected independently.
            overlap = any(j != index and min(region.x + region.width, other.x + other.width) > max(region.x, other.x)
                          and min(region.y + region.height, other.y + other.height) > max(region.y, other.y)
                          for j, other in enumerate(regions))
            if overlap:
                decision.update(angle=None, status="印章区域重叠，保留原方向")
            else:
                page.paste(page.crop(box).transpose(Image.Transpose.ROTATE_180), box)
                decision.update(applied_rotation=180, status="已自动旋转 180°，后续 OCR 使用校正图")
        decisions[index] = decision
    corrected = directory / "orientation-working-page.png"
    if any(row["applied_rotation"] for row in decisions.values()):
        page.save(corrected)
        return corrected, decisions
    return source, decisions
