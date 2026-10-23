"""Corroborate a source-chroma, normal-offset view of moderate oval seals."""
from dataclasses import replace
import math
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.crops import _seal_source_crop, _enlarge_seal
from receipt_ocr.imaging.ellipse import _ellipse_normal_maps, _save_ellipse_annulus_unwrapped
from .orientation_geometry import _rotation_geometry


def prepare_oval_ring(source, region, working, mask_path, guide, rotations,
                     destination, *, reader, provider):
    """Return an independently verified input; never infer text from a requirement.

    Normals shear side glyphs less than centre rays, but converge on narrow
    oval tips. Leave elongated seals on the existing proportional sampler.
    Both fixed chroma gains must agree at high confidence before selection.
    The observed border fit, original pixels and exact centre ink mask are
    reused; neither expected names nor previously recognized names are inputs.
    """
    if region.color not in {"red", "blue"} or guide is None or rotations is None:
        return None
    axes = guide.annulus[0][2][1]
    ratio = min(axes) / max(axes)
    # At this ratio the normal sampler's .55*b depth stays below its
    # .85*b*b/a intersection limit, with some margin for border error.
    if ratio < .70:
        return None
    clean = cv2.imread(str(working), cv2.IMREAD_COLOR)
    if clean is None or clean.shape[:2] != guide.source_shape:
        return None
    coverage = np.zeros(guide.source_shape, dtype=bool)
    if mask_path is not None:
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != guide.source_shape:
            return None
        coverage = mask > 0
    original = _seal_source_crop(source, region)
    b, g, r = (channel.astype(np.float32) for channel in cv2.split(original))
    # Difference at native resolution, before interpolation can mix neutral
    # form ink and coloured stamp ink into new coloured edge pixels.
    chroma = np.maximum(r - np.maximum(g, b) if region.color == "red"
                        else b - np.maximum(g, r), 0)
    mx, my = _ellipse_normal_maps(*guide.annulus)
    normal = replace(guide, map_x=mx, map_y=my)
    ink_mask = _ocr_color_mask(clean, region.color)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    views = []
    for gain in (2.4, 3.0):
        native = np.rint(255 - np.clip(chroma * gain, 0, 255)).astype(np.uint8)
        gray = _enlarge_seal(native)
        for angle in rotations:
            if not math.isfinite(angle):
                return None
            h, w = gray.shape
            matrix, width, height = _rotation_geometry(w, h, angle)
            gray = cv2.warpAffine(gray, matrix, (width, height),
                                  flags=cv2.INTER_CUBIC, borderValue=255)
        if gray.shape != guide.source_shape:
            return None
        covered = gray.copy()
        covered[coverage] = 255
        ring = destination.with_name(f"{destination.stem}-{gain:g}.png")
        if not _save_ellipse_annulus_unwrapped(
                covered, ink_mask, ring, single_line=True,
                ring_guide=normal, proportional=True):
            return None
        reads = reader.read_line(ring, provider=provider)
        text = "".join(item.text for item in reads).strip()
        score = min((item.confidence for item in reads), default=0)
        views.append({"gain": gain, "text": text, "confidence": float(score),
                      "ring": ring, "gray": gray, "covered": covered})
    first, second = views
    if (not first["text"] or first["text"] != second["text"]
            or min(first["confidence"], second["confidence"]) < .97):
        return None
    # Prefer the gentler gain once both observations corroborate the text.
    for key, image in (("source", first["gray"]), ("input", first["covered"])):
        colored = np.full((*image.shape, 3), 255, dtype=np.uint8)
        other_channels = (0, 1) if region.color == "red" else (1, 2)
        colored[:, :, other_channels] = image[:, :, None]
        path = destination.with_name(f"{destination.stem}-{key}.png")
        if not cv2.imwrite(str(path), colored):
            raise ValueError("椭圆章原色展开输入保存失败")
        first[key] = path
    return {
        "ring": first["ring"], "source": first["source"], "input": first["input"],
        "guide": normal,
        "evidence": {"method": "native_chroma_fitted_ellipse_normals",
                     "axis_ratio": round(ratio, 4), "selected_gain": first["gain"],
                     "checks": [{k: view[k] for k in ("gain", "text", "confidence")}
                                for view in views]},
    }
