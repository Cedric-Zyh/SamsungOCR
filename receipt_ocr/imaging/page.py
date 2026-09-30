"""Whole-page QR, region text and annotations."""
from __future__ import annotations

from pathlib import Path
import cv2
from ..runtime.execution import timed
from .contracts import SealRegion
from .io import _read_image


@timed('qr_decode')
def decode_qr(path: str | Path) -> str:
    image = _read_image(path)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    return text or ""


def extract_region_text(
    observations: list,
    region: SealRegion,
    *,
    padding: float = 0.015,
) -> str:
    texts: list[tuple[float, float, str]] = []
    x1, y1 = region.x - padding, region.y - padding
    x2 = region.x + region.width + padding
    y2 = region.y + region.height + padding
    for row in observations:
        cx, cy = row.x + row.width / 2, row.y + row.height / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            texts.append((row.y, row.x, row.text))
    return "".join(text for _, _, text in sorted(texts))


@timed('preview_generation')
def annotate_image(
    source: str | Path,
    destination: str | Path,
    seal_regions: list[SealRegion],
    date_box: tuple[float, float, float, float] | None = None,
) -> None:
    image = _read_image(source)
    height, width = image.shape[:2]
    for region in seal_regions:
        x1, y1 = int(region.x * width), int(region.y * height)
        x2 = int((region.x + region.width) * width)
        y2 = int((region.y + region.height) * height)
        color = (40, 42, 230) if region.color == "red" else (230, 120, 30)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, max(3, width // 600))
    if date_box:
        x, y, w, h = date_box
        cv2.rectangle(
            image,
            (int(x * width), int(y * height)),
            (int((x + w) * width), int((y + h) * height)),
            (48, 180, 65),
            max(3, width // 600),
        )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower() or ".jpg"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        raise ValueError("标注图片编码失败")
    encoded.tofile(str(destination))
