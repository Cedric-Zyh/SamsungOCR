"""Separate a small outer serial-number band from upright company lettering."""
from pathlib import Path

import cv2
import numpy as np

from .io import _read_image, _write_stage_image


def save_company_ring_input(source, destination):
    """Exclude an isolated run of small outer glyphs before company OCR.

    On round company seals the oppositely oriented serial number unfolds as
    small glyphs against the top (outer) edge. Require a long, consistent run,
    larger company glyphs elsewhere, and blank space below the entire run.
    This is a conservative layout gate, not a text-level digit replacement or
    a general orientation classifier. Ambiguous layouts keep the full input.
    """
    image = _read_image(source)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    ink = (gray < 180).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components = [tuple(map(int, row)) for row in stats[1:] if row[4] >= 12]
    large = [row for row in components
             if row[3] >= height * .50 and row[2] >= height * .20]
    if len(large) < 3:
        return None

    small = sorted(row for row in components
                   if height * .14 <= row[3] <= height * .38
                   and row[1] + row[3] <= height * .45
                   and row[2] <= height * .40)
    groups = []
    for row in small:
        if (groups and row[0] - (groups[-1][-1][0] + groups[-1][-1][2]) <= height * .30
                and abs(row[1] - groups[-1][0][1]) <= height * .10):
            groups[-1].append(row)
        else:
            groups.append([row])

    keep = np.ones(width, dtype=bool)
    excluded = []
    pad = max(2, round(height * .02))
    for group in groups:
        if len(group) < 6 or max(row[3] for row in group) > 1.6 * min(row[3] for row in group):
            continue
        left = max(0, min(row[0] for row in group) - pad)
        right = min(width, max(row[0] + row[2] for row in group) + pad)
        top = min(row[1] for row in group)
        bottom = max(row[1] + row[3] for row in group)
        # Detached upper strokes of Chinese characters must not be mistaken
        # for numbers. Also inspect the padded columns that would be removed.
        below = ink[min(height, bottom + pad):, left:right]
        if below.size == 0 or np.mean(below) > .005:
            continue
        keep[left:right] = False
        excluded.append({"box": [left, top, right, bottom], "glyph_count": len(group)})

    if not excluded:
        return None
    # Join the remaining arc segments without rescaling their glyphs. The
    # existing circular seam reorder still handles a split company suffix.
    output = image[:, keep]
    _write_stage_image(Path(destination), output, ".png")
    return {
        "method": "small_outer_number_band",
        "excluded": excluded,
        "source_size": [width, height],
        "output_size": [output.shape[1], height],
    }
