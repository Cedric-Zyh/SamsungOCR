from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.image_processing import _color_masks, _read_image, detect_seal_regions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--output", default="tmp/unwrapped")
    args = parser.parse_args()
    source = Path(args.image)
    output = Path(args.output) / source.stem
    output.mkdir(parents=True, exist_ok=True)
    image = _read_image(source)
    ih, iw = image.shape[:2]
    regions = [item for item in detect_seal_regions(source) if item.role == "收货客户章"]
    for index, region in enumerate(regions):
        x1, y1 = int(region.x * iw), int(region.y * ih)
        x2, y2 = int((region.x + region.width) * iw), int((region.y + region.height) * ih)
        crop = image[y1:y2, x1:x2]
        red, blue = _color_masks(crop)
        mask = red if region.color == "red" else blue
        canvas = np.full(mask.shape, 255, dtype=np.uint8)
        canvas[mask > 0] = 0
        h, w = canvas.shape
        size = max(h, w)
        square = np.full((size, size), 255, dtype=np.uint8)
        ox, oy = (size - w) // 2, (size - h) // 2
        square[oy:oy+h, ox:ox+w] = canvas
        radius = size / 2
        polar = cv2.warpPolar(
            square,
            (int(radius), 1440),
            (size / 2, size / 2),
            radius,
            cv2.WARP_POLAR_LINEAR | cv2.WARP_FILL_OUTLIERS,
        )
        for start in (0.42, 0.5, 0.58):
            outer = polar[:, int(radius * start):]
            strip = cv2.rotate(outer, cv2.ROTATE_90_COUNTERCLOCKWISE)
            strip = cv2.resize(strip, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            cv2.imwrite(str(output / f"seal-{index}-outer-{start}.png"), strip)


if __name__ == "__main__":
    main()
