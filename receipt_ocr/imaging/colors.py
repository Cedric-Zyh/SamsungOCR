"""Color masks for seal ink."""
from __future__ import annotations

import cv2
import numpy as np


def _color_masks(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red_a = cv2.inRange(hsv, (0, 55, 70), (16, 255, 255))
    red_b = cv2.inRange(hsv, (164, 45, 65), (179, 255, 255))
    red = cv2.bitwise_or(red_a, red_b)
    blue = cv2.inRange(hsv, (82, 45, 55), (140, 255, 255))
    # Very pale stamps can have too little saturation for HSV even though the
    # ink channel remains slightly dominant. Channel-difference masks retain
    # those strokes without admitting neutral black/gray form text.
    b, g, r = (channel.astype(np.int16) for channel in cv2.split(image))
    pale_red = np.where(
        (r >= 105) & (r - np.maximum(g, b) >= 5), 255, 0
    ).astype(np.uint8)
    red = cv2.bitwise_or(red, pale_red)
    return red, blue


def _ocr_color_mask(crop: np.ndarray, color: str) -> np.ndarray:
    """Return stamp ink while rejecting neutral form-line fringes.

    ``_color_masks`` is deliberately permissive because it is used to find a
    faint stamp on a whole page.  That permissiveness is harmful after a
    customer-stamp crop: antialiased black table rules can acquire a small
    red-channel bias and survive as horizontal fragments in the OCR image.
    The OCR derivative therefore uses a stricter chroma gate and removes only
    long, nearly-horizontal *low-saturation* runs.  Red/blue pixels are never
    removed by the line pass, so a coloured character crossing a form rule is
    preserved.
    """
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    if color == "red":
        hue_match = (hue <= 16) | (hue >= 164)
    else:
        hue_match = (hue >= 82) & (hue <= 140)
    # Keep the gate below the saturation of faint scanned red lettering.  The
    # neutral-line pass below removes long low-saturation table rules, so this
    # lower value does not re-admit the black form rows that the colour-safe
    # derivative is meant to exclude.
    mask = (
        hue_match
        & (saturation >= 45)
        & (value >= 50)
    ).astype(np.uint8) * 255

    if mask.size:
        # Detect only neutral dark lines.  The long horizontal opening avoids
        # treating individual Chinese strokes as a form line; the small
        # dilation clears antialiased edge pixels without touching coloured
        # ink because the subtraction is restricted to ``mask``.
        neutral = ((saturation < 55) & (value < 215)).astype(np.uint8) * 255
        kernel_width = max(24, mask.shape[1] // 14)
        horizontal = cv2.morphologyEx(
            neutral,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1)),
        )
        horizontal = cv2.dilate(
            horizontal,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)),
        )
        mask = cv2.bitwise_and(mask, cv2.bitwise_not(horizontal))
    return mask
