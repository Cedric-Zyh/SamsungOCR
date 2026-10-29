"""Stamp orientation: rectangle orientation."""

from receipt_ocr.runtime.image_processing import image_operation

from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from receipt_ocr.imaging.crops import save_isolated_seal, save_region_crop
from receipt_ocr.recognition.seal.preprocess.orientation_angles import choose_rectangular_stamp_angle, decide_orientation
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry
from receipt_ocr.providers.orientation import classify_lines


def _check_rectangle_direction(image, boxes, matrix, padding, destination, model_variant):
    """Compare the same first-detected rows; digits alone never choose 180°."""
    from .rectangle_layout import _direction_score
    from ..ocr.interface import read_line

    initial_score = _direction_score(boxes)
    result = {"method": "fixed_rows_0_180", "initial_score": initial_score,
              "selected_rotation": 0, "reads": {}}
    if initial_score >= 4:
        result["reason"] = "confident_original_chinese"
        return boxes, result
    if not any(sum('\u4e00' <= c <= '\u9fff' for c in str(b.get('text') or '')) >= 2 for b in boxes):
        result["reason"] = "no_chinese_direction_anchor"
        return boxes, result
    height, width = image.shape[:2]
    readings = {0: {}, 180: {}}
    for i, box in enumerate(boxes):
        points = np.asarray(box.get("points", []), np.float32)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        mapped = cv2.transform((points + padding)[None], matrix)[0]
        low = np.maximum(0, np.floor(mapped.min(0)).astype(int) - 3)
        high = np.minimum([width, height], np.ceil(mapped.max(0)).astype(int) + 4)
        if np.any(high <= low) or high[0]-low[0] < 2*(high[1]-low[1]):
            continue
        crop = cv2.copyMakeBorder(image[low[1]:high[1], low[0]:high[0]],
                                  16, 16, 16, 16, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        for coarse in (0, 180):
            path = destination.with_name(f"{destination.stem}-direction-{i}-{coarse}.png")
            pixels = crop
            if coarse:
                with image_operation('rotation', '旋转校正'):
                    pixels = cv2.rotate(crop, cv2.ROTATE_180)
            if not cv2.imwrite(str(path), pixels):
                raise ValueError("矩形章正反方向判断图保存失败")
            observations = read_line(path, model_variant=model_variant)
            if len(observations) != 1:
                continue
            observation = observations[0]
            if not np.isfinite(observation.confidence) or not observation.text.strip():
                continue
            readings[coarse][i] = {"text": observation.text.strip(),
                                    "confidence": float(observation.confidence),
                                    "points": box['points'], "image_path": str(path)}
    scores = {coarse: _direction_score(list(values.values())) for coarse, values in readings.items()}
    selected = 180 if scores[180] >= 3.6 and scores[180] > max(initial_score, scores[0]) + 3 else 0
    reason = 'opposite_chinese_verified' if selected else 'kept_original_direction'
    if not selected and scores[180] >= 3.6:
        reason = 'ambiguous_directions'
    result.update(selected_rotation=selected, original_score=scores[0], opposite_score=scores[180],
                  reads={str(k): list(v.values()) for k, v in readings.items()},
                  reason=reason)
    if scores[selected] < 3.6:
        return boxes, result
    # Geometry remains that of the initial detection; the final-frame crops
    # will be read again after the whole image has taken the chosen direction.
    verified = []
    for i, box in enumerate(boxes):
        read = readings[selected].get(i)
        if read and read['confidence'] >= .75:
            verified.append({**box, "text": read['text'], "confidence": read['confidence'],
                             "direction_verified": True})
        else:
            verified.append(box)
    result['verified_rows'] = True
    return verified, result


def prepare_rectangular_stamp(source, destination, *, model_variant="v6"):
    """Pad and deskew a rectangular stamp before its body OCR pass."""
    from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

    source, destination = Path(source), Path(destination)
    boxes = detect_boxes(source, model_variant=model_variant)
    decision = choose_rectangular_stamp_angle(boxes)
    decision.update(mode="rectangle_text_angle", model_variant=model_variant,
                    detected_boxes=boxes)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError(f"无法读取矩形章方向校正图：{source}")
    height, width = image.shape[:2]
    padding = max(12, min(48, int(round(min(height, width) * 0.03))))
    padded = cv2.copyMakeBorder(
        image, padding, padding, padding, padding,
        cv2.BORDER_CONSTANT, value=(255, 255, 255),
    )
    angle = float(decision.get("applied_rotation") or 0.0)
    matrix = np.array([[1., 0., 0.], [0., 1., 0.]])
    if abs(angle) >= 0.01:
        padded_height, padded_width = padded.shape[:2]
        matrix, new_width, new_height = _rotation_geometry(
            padded_width, padded_height, angle
        )
        with image_operation('rotation', '旋转校正'):
            output = cv2.warpAffine(
                padded,
                matrix,
                (new_width, new_height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255, 255, 255),
            )
        decision["status"] = "矩形章已按横向文字行旋正并加白边"
    else:
        output = padded
        decision["status"] = "矩形章保持原方向并加白边"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        verified, direction = _check_rectangle_direction(
            output, boxes, matrix, padding, destination, model_variant,
        )
        decision['direction_check'] = direction
        decision['detected_boxes'] = verified
        if direction.get('verified_rows'):
            decision['force_row_read'] = True
        if direction['selected_rotation'] == 180:
            h, w = output.shape[:2]
            flip = np.array([[-1., 0., w-1], [0., -1., h-1], [0., 0., 1.]])
            matrix = (flip @ np.vstack([matrix, [0., 0., 1.]]))[:2]
            with image_operation('rotation', '旋转校正'):
                output = cv2.rotate(output, cv2.ROTATE_180)
            angle += 180
            decision['status'] = '矩形章已验证正反方向，旋转 180° 并校平文字'
        elif direction.get('reason') == 'ambiguous_directions' or (
            not direction.get('verified_rows') and direction.get('reason') != 'confident_original_chinese'
        ):
            decision['status'] = '矩形章已加白边并校平，正反方向待确认'
    except Exception as exc:
        decision['direction_check'] = {'selected_rotation': 0, 'error': str(exc)}
        decision['status'] = '矩形章已加白边并校平，正反方向判断失败'
    decision['rotation_matrix'] = matrix.tolist()
    decision['rotation_steps'] = [angle] if abs(angle) >= .01 else []
    decision["padding"] = padding
    decision["oriented_path"] = str(destination)
    decision["applied_rotation"] = round(angle, 3)
    if not cv2.imwrite(str(destination), output):
        raise ValueError(f"矩形章方向校正图写入失败：{destination}")
    return destination, decision

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

def prepare_rectangles(source, regions, directory):
    """Make a temporary page copy, rotating only confirmed rectangle boxes.

    Keeping page coordinates unchanged means every later crop/evidence reads the
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
