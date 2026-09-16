"""Render the single stamp selected by QingTong, without another OCR request."""

from io import BytesIO
from math import ceil, floor, isfinite
from pathlib import Path

from PIL import Image, ImageOps


def selected_seal_xyxy(check: dict) -> tuple[float, float, float, float] | None:
    dual = check.get("dual_check") or {}
    if not isinstance(dual, dict) or dual.get("policy") not in {
        "qingtong_template_and_ocr", "qingtong_any_channel", "qingtong_all_channel",
    }:
        return None
    if (check.get("api") or {}).get("ok") is False:
        return None
    selected = dual.get("selected")
    box = selected.get("xyxy") if isinstance(selected, dict) else None
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not isfinite(value) for value in box):
        return None
    x1, y1, x2, y2 = box
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def render_selected_seal(source: Path, check: dict) -> BytesIO:
    """Crop pixel coordinates on the original, upright image; retain its colors."""
    box = selected_seal_xyxy(check)
    if box is None:
        raise ValueError("清瞳未保存有效的判定印章位置")
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        width, height = image.size
        x1, y1 = max(0, floor(box[0])), max(0, floor(box[1]))
        x2, y2 = min(width, ceil(box[2])), min(height, ceil(box[3]))
        if x2 - x1 < 2 or y2 - y1 < 2:
            raise ValueError("印章位置不在原图范围内")
        # A small margin keeps the stamped border and nearby paper visible.
        margin = max(8, min(40, ceil(min(x2 - x1, y2 - y1) * .06)))
        crop = image.crop((max(0, x1 - margin), max(0, y1 - margin),
                           min(width, x2 + margin), min(height, y2 + margin)))
        result = BytesIO()
        crop.convert("RGB").save(result, format="PNG")
    result.seek(0)
    return result
