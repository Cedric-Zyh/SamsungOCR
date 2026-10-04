"""Stamp orientation: rectangle orientation."""

from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from receipt_ocr.imaging.crops import save_isolated_seal, save_region_crop
from receipt_ocr.recognition.seal.preprocess.orientation_angles import choose_rectangular_stamp_angle, decide_orientation
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry
from receipt_ocr.providers.orientation import classify_lines


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
    if abs(angle) >= 0.01:
        padded_height, padded_width = padded.shape[:2]
        matrix, new_width, new_height = _rotation_geometry(
            padded_width, padded_height, angle
        )
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
    decision["padding"] = padding
    decision["oriented_path"] = str(destination)
    decision["applied_rotation"] = round(angle, 3)
    destination.parent.mkdir(parents=True, exist_ok=True)
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
