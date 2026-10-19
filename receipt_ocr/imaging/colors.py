"""Color masks for seal ink."""
from __future__ import annotations

import cv2
import numpy as np


def _red_ink_strength(crop: np.ndarray) -> np.ndarray:
    """Retain observed grey-red strokes while suppressing neutral black ink.

    Colour establishes stroke membership, not its darkness: faint chroma can
    belong to a dark stamp glyph. Work at native resolution so neighbouring
    support and black-edge rejection do not grow with OCR upscaling.
    """
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(crop))
    neutral = np.maximum(g, b)
    signed_difference = r - neutral
    difference = np.maximum(0, signed_difference)
    darkness = 255 - neutral
    hue = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 0]
    valid = (difference >= 5) & ((hue <= 16) | (hue >= 164))

    # Keep a continuous chroma baseline for sparse red pixels. The connected
    # stroke pass below additionally preserves their observed darkness.
    weight = np.clip((difference - 3) / 22, 0, 1)
    weight = weight * weight * (3 - 2 * weight)
    strength = np.maximum(difference * 1.6, (255 - neutral) * weight)
    strength = np.clip(strength, 0, 235)
    strength[~valid] = 0

    # Find long dark rules in either direction. Chroma-biased JPEG fragments
    # may evade an HSV-neutral mask. Only remove weakly chromatic dark pixels
    # on these rules; protect pale red and saturated dark-red crossings.
    dark = (r < 155).astype(np.uint8) * 255
    lines = np.zeros_like(dark)
    for kernel_size in ((max(35, crop.shape[1] // 4), 1),
                        (1, max(35, crop.shape[0] // 4))):
        lines |= cv2.morphologyEx(
            dark, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size),
        )
    lines = cv2.dilate(lines, np.ones((3, 3), dtype=np.uint8))
    rule_ink = ((lines > 0) & (r < 180) & (difference < 30)
                & (difference / np.maximum(r, 1) < .35))
    strength[rule_ink] = 0

    red_hue = (hue <= 20) | (hue >= 160)
    seed = (signed_difference >= 4) & red_hue & ~rule_ink
    support = ((signed_difference >= 2) & red_hue
               & (darkness >= 8) & ~rule_ink)
    nearby = cv2.distanceTransform(
        (~seed).astype(np.uint8), cv2.DIST_L2, 5
    ) <= 1.5
    support |= (nearby & (signed_difference >= 0)
                & (darkness >= 16) & ~rule_ink)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        support.astype(np.uint8), 8
    )
    seed_counts = np.bincount(labels[seed], minlength=count)
    accepted_labels = (stats[:, cv2.CC_STAT_AREA] >= 2) & (seed_counts > 0)
    accepted_labels[0] = False
    accepted = accepted_labels[labels]
    # Preserve existing intensity with a modest gain. This does not draw new
    # strokes or fill pixels covered by opaque handwriting.
    strength[accepted] = np.maximum(
        strength[accepted], np.clip(darkness[accepted] * 1.2, 0, 225)
    )

    # A tiny red bias on dark form text must not turn its black core into a
    # red glyph. Reject the core and only weakly coloured neighbouring edges;
    # stronger red crossings and lighter grey-red lettering remain eligible.
    red_fraction = signed_difference / np.maximum(darkness, 1)
    black_core = (r < 115) & (red_fraction < .03)
    near_black = cv2.distanceTransform(
        (~black_core).astype(np.uint8), cv2.DIST_L2, 5
    ) <= 1.5
    strength[black_core | (near_black & (red_fraction < .08))] = 0
    return strength


def _isolate_red_ink(crop: np.ndarray) -> np.ndarray:
    """Render supported stamp strokes as red on a white canvas."""
    strength = _red_ink_strength(crop)
    result = np.full_like(crop, 255)
    result[:, :, 0] = result[:, :, 1] = np.rint(255 - strength).astype(np.uint8)
    return result


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
    if color == "red":
        b, g, r = (channel.astype(np.int16) for channel in cv2.split(crop))
        # Pale red lettering still has chromatic evidence even when its HSV
        # saturation falls below 45. Keep bright red-biased pixels; neutral
        # gray/black print has no such channel separation.
        pale_red = (r >= 180) & (r - np.maximum(g, b) >= 5) & hue_match
        mask[pale_red] = 255

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
