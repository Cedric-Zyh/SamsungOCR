"""Prepare measured rectangle rows or one canonical multi-column OCR input."""
from pathlib import Path
import re

import cv2
import numpy as np

from receipt_ocr.imaging.io import _read_image, _write_stage_image
from .rectangle_frame import geometry_threshold, measure_frame, row_spans
from .rectangle_columns import bounds, column_groups, reading_order


def _empty_form_field(interior, span, boxes, scaled_height):
    """Confirm an empty caption from independent detection and source ink.

    Row OCR can misread a pale caption. Keep that reading verbatim, but do
    not turn it into stamp-name text when its cell has a verified caption
    and no separate value ink. Filled fields never enter this path.
    """
    height, width = interior.shape[:2]
    top, bottom = span
    selected, labels = [], []
    for box in boxes:
        points = np.asarray(box.get('points', []), float)
        confidence = float(box.get('confidence') or 0)
        if (points.shape != (4, 2) or not np.isfinite(points).all() or not np.isfinite(confidence)
                or np.any(points.max(0) <= points.min(0))):
            continue
        mapped = (points-32)*[width/960, height/scaled_height]
        if not top <= mapped[:, 1].mean() < bottom or confidence < .5:
            continue
        text = re.sub(r'\s+', '', str(box.get('text') or ''))
        if re.fullmatch(r'(?:收货人|收货日期|收货时间|签收人|签收日期)[:：]?', text) and confidence >= .90:
            labels.append(text)
        elif not re.fullmatch(r'[年月日时分:：]+', text):
            return None
        selected.append(mapped)
    if len(labels) != 1:
        return None
    glyph_height = float(np.median([np.ptp(p[:, 1]) for p in selected]))
    allowed = np.zeros((bottom-top, width), np.uint8)
    pad = max(3, round(glyph_height * .12))
    for points in selected:
        lo = np.floor(points.min(0)).astype(int)-pad
        hi = np.ceil(points.max(0)).astype(int)+pad+1
        allowed[max(0,lo[1]-top):min(bottom-top,hi[1]-top), max(0,lo[0]):min(width,hi[0])] = 1
    from .rectangle_frame import red_ink
    remaining = red_ink(interior[top:bottom], 25)
    remaining[allowed > 0] = 0
    remaining[:, :2] = remaining[:, -2:] = 0
    _, _, stats, _ = cv2.connectedComponentsWithStats(remaining, 8)
    if any(s[3] >= glyph_height * .20 and s[4] >= max(12, glyph_height**2 * .02)
           for s in stats[1:]):
        return None
    return {'label': labels[0], 'method': 'detected_caption_and_empty_ink'}


def _canonical(interior):
    height, width = interior.shape[:2]
    scaled_height = round(height * 960 / width)
    pixels = cv2.resize(interior, (960, scaled_height),
                        interpolation=cv2.INTER_CUBIC if width < 960 else cv2.INTER_AREA)
    return cv2.copyMakeBorder(pixels, 32, 32, 32, 32, cv2.BORDER_CONSTANT,
                              value=(255, 255, 255)), scaled_height


def _direction_score(boxes):
    # Upside-down digits can still be read confidently. Require a complete
    # Chinese text row as evidence, independently of the requested company.
    score = 0.0
    for box in boxes:
        points = np.asarray(box.get("points", []), dtype=float)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or np.any(points.max(0) <= points.min(0))):
            continue
        confidence = float(box.get("confidence") or 0)
        count = sum("\u4e00" <= ch <= "\u9fff" for ch in str(box.get("text") or ""))
        if confidence >= .90 and count >= 4:
            score += count * confidence
    return score


def prepare_frame_layout(source, directory, index, prefix, provider, mode, reader):
    original = _read_image(source)
    image, crop, angle = measure_frame(original)
    left, top, right, bottom = crop
    interior = image[top:bottom, left:right]
    height, width = interior.shape[:2]
    canonical, scaled_height = _canonical(interior)
    inputs, jobs = [], []

    def save(name, pixels, label, channel=None):
        path = Path(directory) / f"seal-{index}-{name}.png"
        _write_stage_image(path, pixels, ".png")
        item = {"id": f"seal-{index}:{name}", "name": name, "label": label,
                "channel": channel, "ocr_input": channel is not None,
                "image_url": f"{prefix.rstrip('/')}/{path.name}" if prefix else ""}
        inputs.append(item)
        return item, path

    detector, detector_path = save("rectangle-layout", canonical, "矩形内框 · 固定尺度排版检测")
    detected = reader.detect_boxes(detector_path, provider=provider, model_variant="v6")
    coarse = 0
    direction = {"original_score": _direction_score(detected), "selected_rotation": 0}
    # Strong upright text already fixes the direction. Otherwise compare the
    # opposite direction using its own complete frame, not clipped OCR boxes.
    if direction["original_score"] < 4:
        try:
            opposite, opposite_crop, opposite_angle = measure_frame(cv2.rotate(original, cv2.ROTATE_180))
            l2, t2, r2, b2 = opposite_crop
            opposite_canonical, opposite_height = _canonical(opposite[t2:b2, l2:r2])
            _, opposite_path = save("rectangle-direction-180", opposite_canonical, "180° 方向判断输入")
            opposite_boxes = reader.detect_boxes(opposite_path, provider=provider, model_variant="v6")
            direction["opposite_score"] = _direction_score(opposite_boxes)
            if direction["opposite_score"] >= 3.6 and direction["opposite_score"] > direction["original_score"] + 3:
                image, crop, angle = opposite, opposite_crop, opposite_angle
                left, top, right, bottom = crop
                interior = image[top:bottom, left:right]
                height, width = interior.shape[:2]
                canonical, scaled_height = opposite_canonical, opposite_height
                detected, coarse = opposite_boxes, 180
                direction["selected_rotation"] = coarse
                _write_stage_image(detector_path, canonical, ".png")
        except Exception as exc:
            direction["opposite_error"] = str(exc)
    boxes = []
    for detection_index, box in enumerate(detected):
        points = np.asarray(box.get("points", []), dtype=float)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or not str(box.get("text") or "").strip() or float(box.get("confidence") or 0) < .75):
            continue
        if np.any(points.max(0) <= points.min(0)):
            continue
        boxes.append(dict(box, detection_index=detection_index))
    groups = column_groups(boxes)
    spans = row_spans(interior) if not groups else []
    if not groups and not spans:
        raise ValueError("矩形内框没有可分离的文字行")
    oriented, _ = save("oriented", image, "方向校正图 · 保留完整边框")
    overlay = image.copy()
    cv2.rectangle(overlay, (left, top), (right, bottom), (65, 110, 65), 2)
    frame, _ = save("rectangle-frame", overlay, "检测到的矩形内框")
    rows, urls = [], []
    if groups:
        order = reading_order(boxes, groups)
        for row_index, detection_index in enumerate(order):
            box = boxes[detection_index]
            x1, y1, x2, y2 = bounds(box)
            points = np.asarray(box["points"], dtype=float)
            mapped = (points - 32) * [width / 960, height / scaled_height] + [left, top]
            rows.append({"text": str(box["text"]).strip(), "confidence": float(box["confidence"]),
                         "detection_index": box["detection_index"], "points": points.tolist(),
                         "oriented_points": mapped.tolist(), "coordinate_space": "rectangle-layout",
                         "x": float(x1 / canonical.shape[1]), "y": float(y1 / canonical.shape[0]),
                         "width": float((x2 - x1) / canonical.shape[1]),
                         "height": float((y2 - y1) / canonical.shape[0])})
            x1, y1 = np.maximum(0, np.floor([x1, y1]).astype(int) - 2)
            x2, y2 = np.minimum(canonical.shape[1::-1], np.ceil([x2, y2]).astype(int) + 3)
            if x2 <= x1 or y2 <= y1:
                raise ValueError("矩形文字块超出内框范围")
            preview, _ = save(f"rectangular-row-{row_index}", canonical[y1:y2, x1:x2],
                              "矩形文字块 · 检测结果预览")
            urls.append(preview["image_url"])
        detector.update(channel="body", ocr_input=True, detected_rows=rows,
                        label="矩形内框 · 多栏 OCR 实际输入")
        jobs.append((detector, detector_path))
    else:
        for row_index, (a, b) in enumerate(spans):
            pixels = cv2.copyMakeBorder(interior[a:b], 20, 20, 20, 20,
                                        cv2.BORDER_CONSTANT, value=(255, 255, 255))
            item, path = save(f"rectangular-row-{row_index}", pixels, "矩形整行 · OCR 实际输入", "body")
            item.update(read_mode="line", rectangle_row_index=row_index)
            rows.append({"crop_box": [left, top + a, right, top + b], "coordinate_space": "oriented",
                         "path": str(path)})
            if len(spans) >= 3:
                field = _empty_form_field(interior, (a, b), detected, scaled_height)
                if field:
                    rows[-1]['empty_form_field'] = field
            urls.append(item["image_url"])
            jobs.append((item, path))
    metadata = {
        "color_isolated_oriented_url": oriented["image_url"],
        "body_input_id": detector["id"] if groups else oriented["id"],
        "body_url": detector["image_url"] if groups else oriented["image_url"],
        "rectangular_frame_url": frame["image_url"],
        "rectangular_layout": "frame_columns" if groups else "frame_rows",
        "rectangular_fixed_rows": rows, "rectangular_row_urls": urls,
        "rectangular_frame": {"crop_box": crop, "measurement_width": 1000,
                              "canonical_width": 960, "padding": 32,
                              "geometry_threshold": geometry_threshold(original),
                              "direction": direction,
                              "detected_boxes": detected, "column_groups": groups},
        "orientation": {"mode": mode, "status": "按矩形边框校正", "applied_rotation": coarse + angle,
                        "rotation_steps": ([coarse] if coarse else []) + [angle], "method": "rectangle_rim"},
    }
    return metadata, inputs, jobs
