"""Non-generative date preprocessing: preserve dark ink and frame crossings."""

import cv2
import numpy as np
from PIL import Image


def suppress_red_stamp(image: Image.Image) -> Image.Image:
    """Suppress bright red ink while protecting dark handwriting.

    A red-channel-only image makes the stamp light but also changes the
    relative shape of black strokes.  Instead, start from luminance and
    whiten only pixels that are both bright and strongly red.  Dark mixed
    pixels at a stamp/pen intersection remain available to OCR.
    """
    rgb = np.asarray(image.convert("RGB")).copy()
    red, green, blue = (rgb[:, :, index].astype(np.int16) for index in (0, 1, 2))
    red_excess = red - np.maximum(green, blue)
    bright_red = (
        (red_excess >= 35)
        & (np.minimum(green, blue) >= 140)
        & (red >= 160)
    )
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray[bright_red] = 255
    return Image.fromarray(gray).convert("RGB")


def remove_date_frame(image: Image.Image) -> Image.Image:
    """Erase long outside rules, protecting pen strokes crossing each rule."""
    rgb = np.asarray(image.convert("RGB")).copy()
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ink = gray < 180
    height, width = ink.shape
    horizontal = cv2.morphologyEx(
        ink.astype(np.uint8), cv2.MORPH_OPEN,
        np.ones((1, max(30, round(width * .5))), np.uint8),
    )
    rows = np.flatnonzero(horizontal.sum(axis=1) > width * .45)
    groups = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
    for group in groups:
        if not len(group):
            continue
        start, stop = max(0, int(group[0]) - 1), min(height, int(group[-1]) + 2)
        if (start + stop) / 2 > height * .4 and (start + stop) / 2 < height * .5:
            continue
        # Nearby ink on both sides indicates a crossing, not bare frame.
        above = ink[max(0, start - 5):start].any(axis=0)
        below = ink[stop:min(height, stop + 5)].any(axis=0)
        above = cv2.dilate(above.astype(np.uint8)[None, :], np.ones((1, 7), np.uint8))[0]
        below = cv2.dilate(below.astype(np.uint8)[None, :], np.ones((1, 7), np.uint8))[0]
        protect = cv2.dilate((above & below)[None, :], np.ones((1, 3), np.uint8))[0].astype(bool)
        rgb[start:stop, ~protect] = 255
    # Only the extreme right rule is eligible; interior 1/7 strokes survive.
    vertical = cv2.morphologyEx(
        ink.astype(np.uint8), cv2.MORPH_OPEN,
        np.ones((max(20, round(height * .6)), 1), np.uint8),
    )
    vertical[:, :round(width * .96)] = 0
    rgb[vertical.astype(bool)] = 255
    return Image.fromarray(rgb)
