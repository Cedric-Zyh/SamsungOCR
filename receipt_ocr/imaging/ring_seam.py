"""Move an unwrapped company line's seam only into a wide observed gap."""
from pathlib import Path

import cv2
import numpy as np

from .io import _read_image, _write_stage_image


def save_ring_with_blank_seam(source, destination):
    """Save a cyclic column shift, without deleting or synthesizing pixels.

    Ignore the upper rim when locating gaps in glyph bodies. A gap must span
    at least 1.5 strip heights; ordinary character spacing and already-correct
    seams keep the original file. The threshold is deliberately conservative
    because moving the seam on a trimmed partial arc can cause OCR omissions.
    Returns audit metadata; the destination exists only when applied.
    """
    image = _read_image(source)
    height, width = image.shape[:2]
    result = {
        "method": "wide_blank_cyclic_shift",
        "applied": False,
        "shift": 0,
        "gap_width": 0,
        "source_size": [width, height],
        "reason": "no_text_body",
    }
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    ink = gray[round(height * .22):round(height * .95)] < 180
    if not ink.size:
        return result
    active = (ink.sum(axis=0) >= max(3, ink.shape[0] * .035)).astype(np.uint8)
    active = cv2.morphologyEx(
        active[None], cv2.MORPH_CLOSE,
        np.ones((1, max(3, round(height * .06))), np.uint8),
    )[0]
    if not active.any():
        return result

    # Search two copies so a gap crossing the ends is treated as one gap.
    best_start, best_width, start = 0, 0, None
    for index, occupied in enumerate(np.append(np.tile(active, 2), 1)):
        if not occupied and start is None:
            start = index
        elif occupied and start is not None:
            length = index - start
            if best_width < length < width:
                best_start, best_width = start, length
            start = None
    seam = (best_start + best_width // 2) % width
    result.update(gap_width=int(best_width), candidate_shift=int(seam))
    if best_width < 1.5 * height:
        result["reason"] = "gap_too_narrow"
        return result
    if min(seam, width - seam) < .5 * height:
        result["reason"] = "already_at_edge"
        return result

    _write_stage_image(Path(destination), np.roll(image, -seam, axis=1), ".png")
    result.update(applied=True, shift=int(seam), reason="wide_blank_gap")
    return result
