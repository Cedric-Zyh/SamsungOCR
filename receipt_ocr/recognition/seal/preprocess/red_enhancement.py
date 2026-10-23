"""Blend original stamp chroma with restrained enhancement after orientation."""
from pathlib import Path

import cv2
import numpy as np

from .number_recovery import _aligned_reference


def enhance_oriented_seal(source, region, working, destination, orientation):
    """Keep the orientation frame and derive every stroke from source colour.

    Replay the actual rotations on the original crop, rather than rotating
    by their sum: expanded canvases otherwise move the observed text boxes.
    No expected text, glyph completion, or thresholded stroke repair is used.
    """
    rotations = orientation.get("rotation_steps")
    if region.color not in {"red", "blue"} or rotations is None:
        return None
    reference = _aligned_reference(source, region, rotations)
    clean = cv2.imread(str(working), cv2.IMREAD_COLOR)
    if clean is None or reference.shape != clean.shape:
        return None
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(reference))
    chroma = np.maximum(0, r - np.maximum(g, b) if region.color == "red"
                        else b - np.maximum(g, r))
    previous_strength = 255 - clean[:, :, 1].astype(np.float32)
    # Original chroma carries the glyph. Existing enhancement contributes
    # only a small amount, weighted by evidence in the original pixel.
    strength = np.clip(
        chroma * 2 + .15 * previous_strength * chroma / (chroma + 20),
        0, 235,
    )
    enhanced = np.full_like(clean, 255)
    other_channels = (0, 1) if region.color == "red" else (1, 2)
    enhanced[:, :, other_channels] = np.rint(255 - strength).astype(np.uint8)[:, :, None]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), enhanced):
        raise ValueError("旋正章色结合增强图保存失败")
    return destination


def enhance_oriented_red_seal(source, region, working, destination, orientation):
    """Compatibility entry point for callers explicitly requesting red ink."""
    if region.color != "red":
        return None
    return enhance_oriented_seal(source, region, working, destination, orientation)
