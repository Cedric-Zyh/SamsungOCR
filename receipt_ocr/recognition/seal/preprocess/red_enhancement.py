"""Blend original stamp chroma with restrained enhancement after orientation."""

from receipt_ocr.runtime.image_processing import timed_image
from pathlib import Path

import cv2
import numpy as np

from .number_recovery import _aligned_reference
from receipt_ocr.imaging.working_images import read_cv, write_cv


@timed_image('enhance', '图像增强')
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
    clean = read_cv(working)
    if clean is None or reference.shape != clean.shape:
        return None
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(reference))
    chroma = np.maximum(0, r - np.maximum(g, b) if region.color == "red"
                        else b - np.maximum(g, r))
    previous_strength = 255 - clean[:, :, 1].astype(np.float32)
    # Original chroma limits arbitrary remnants in the working image. On its
    # own it would erase grey-red/grey-blue strokes whose darkness was already
    # verified during native-scale isolation (the Tianjin stamp regression).
    strength = np.clip(
        chroma * 2 + .15 * previous_strength * chroma / (chroma + 20),
        0, 235,
    )
    trusted_ink = _aligned_reference(source, region, rotations, isolate_color=True)
    trusted_strength = 255 - trusted_ink[:, :, 1].astype(np.float32)
    # Reuse the original isolation rules, enlargement and each actual rotation.
    # A source-derived floor preserves genuine grey-coloured ink without
    # restoring neutral handwriting or amplified noise from ``working``.
    strength = np.maximum(strength, trusted_strength)
    enhanced = np.full_like(clean, 255)
    other_channels = (0, 1) if region.color == "red" else (1, 2)
    enhanced[:, :, other_channels] = np.rint(255 - strength).astype(np.uint8)[:, :, None]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not write_cv(destination, enhanced):
        raise ValueError("旋正章色结合增强图保存失败")
    return destination


def enhance_oriented_red_seal(source, region, working, destination, orientation):
    """Compatibility entry point for callers explicitly requesting red ink."""
    if region.color != "red":
        return None
    return enhance_oriented_seal(source, region, working, destination, orientation)
