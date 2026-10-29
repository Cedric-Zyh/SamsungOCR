"""Resolve incomplete horizontal detections without consulting required text.

The detector supplies all search locations. Agreeing reads select a complete
line; reading context and the conservative white mask remain separate.
"""

from receipt_ocr.runtime.image_processing import timed_image
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import math
import tempfile

import cv2
import numpy as np

from receipt_ocr.imaging.colors import _ocr_color_mask
from receipt_ocr.imaging.ellipse import _fit_ellipse_annulus, _ellipse_normal_maps
from .detection_scale import detect_orientation_boxes
from .orientation_angles import _stamp_type_marker_strength
from .orientation_geometry import _rotation_geometry, _transform_points, rotate_stamp_image
from .orientation_geometry import rotate_stamp_pixels
from receipt_ocr.imaging.working_images import read_cv, write_cv, cached_transform, copy_image
from ..ocr.interface import write_temporary_input

# Bounded contexts measured in detected row heights, shared by all stamps.
CONTEXTS = ((0, 0, .03, .03), (.35, .35, .03, .03), (.7, .7, .03, .03),
            (.12, 1.1, .1, .2), (.12, 1.5, .1, .2), (.12, 1.5, .1, .4))


def _write(path, image):
    if not write_cv(path, image):
        raise ValueError("横排识别图片保存失败")


def horizontal_groups(boxes, width):
    """Join overlapping upper/lower fragments, including misread fragments."""
    groups = []
    for box in boxes:
        q = np.asarray(box.get("points") or [], dtype=float)
        if q.shape != (4, 2) or not np.isfinite(q).all():
            continue
        dx, dy = q[1] - q[0]
        angle = math.degrees(math.atan2(dy, dx))
        w, h = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0])
        if w <= .35 * width or w <= 1.8 * h or abs(angle) > 22 or len(box.get("text", "")) < 3:
            continue
        lo, hi = q.min(0), q.max(0)
        for group in groups:
            points = np.concatenate([np.asarray(b["points"]) for b in group["boxes"]])
            gl, gh = points.min(0), points.max(0)
            overlap = min(hi[0], gh[0]) - max(lo[0], gl[0])
            gap = max(lo[1], gl[1]) - min(hi[1], gh[1])
            if (overlap > .55 * min(hi[0] - lo[0], gh[0] - gl[0])
                    and gap < .3 * max(hi[1] - lo[1], gh[1] - gl[1])
                    and abs(angle - group["angle"]) < 15):
                group["boxes"].append(box)
                break
        else:
            groups.append({"boxes": [box], "angle": angle})
    return groups


def choose_consensus(trials):
    votes = defaultdict(list)
    for trial in trials:
        if (math.isfinite(trial["confidence"]) and trial["confidence"] >= .85
                and _stamp_type_marker_strength(trial["text"]) >= 2):
            votes[trial["text"]].append(trial)
    eligible = {text: rows for text, rows in votes.items()
                if len({(r["seed"], r["mode"], r["pad"]) for r in rows}) >= 2}
    if not eligible:
        return None
    ranked = sorted(eligible.items(), key=lambda kv: (
        len(kv[1]), float(np.median([r["confidence"] for r in kv[1]]))), reverse=True)
    # Equal support for conflicting complete text is not a reliable decision.
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return None
    text, rows = ranked[0]
    # Keep the displayed/used line colour-isolated when it supports the read.
    clean = [row for row in rows if row["mode"] != "raw"]
    if not clean:
        return None
    winner = max(clean, key=lambda row: row["confidence"])
    support = [r for r in rows if r["seed"] == winner["seed"]]
    if len({(r["mode"], r["pad"]) for r in support}) < 2:
        return None
    boxes = np.asarray([r["box"] for r in support])
    mask_box = [int(boxes[:, 0].max()), int(boxes[:, 1].max()),
                int(boxes[:, 2].min()), int(boxes[:, 3].min())]
    if mask_box[2] <= mask_box[0] or mask_box[3] <= mask_box[1]:
        return None
    return dict(winner, support=len(rows), mask_box=mask_box,
                alternatives={key: len(value) for key, value in votes.items()})


def preserves_verified_row(previous, candidate):
    """A clipped repeated suffix cannot replace an already verified prefix."""
    return not previous or previous in candidate


def ring_evidence_quality(clean, proposed, guide):
    """Limit the soft-colour route to legible, sparse company-letter bands.

    Dense stripe/serial artwork needs its existing layout-specific sampler.
    A colour view that loses established ink must not replace the old input.
    These gates use pixels only, never the required company or type text.
    """
    masks = []
    for source in (clean, proposed):
        strip = cv2.remap(source, guide.map_x, guide.map_y, cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        masks.append(strip.max(2).astype(float) - strip.min(2) > 40)
    old, new = masks
    retained = float(new[old].mean()) if old.any() else 0.
    density = float(new.mean())
    return {"retained_ink": retained, "ink_density": density,
            "accepted": retained >= .85 and 0 < density <= .40}


def prepare_horizontal_consensus(original, color, destination, *, reader, provider,
                                 model_variant, prior=None, ink_color="red"):
    """Return a verified single-row plan, or leave the existing path in charge."""
    destination = Path(destination)
    base, raw = read_cv(color), read_cv(original)
    if base is None or raw is None:
        return None
    bh, bw = base.shape[:2]
    raw = cached_transform(raw, 'resize', ('opencv_cubic', bw, bh),
                           lambda: cv2.resize(raw, (bw, bh), interpolation=cv2.INTER_CUBIC))
    trials, seeds = [], []
    with tempfile.TemporaryDirectory(prefix="seal-horizontal-") as temp:
        scratch = Path(temp)
        for coarse in (0, 90, 180, 270):
            path = scratch / f"coarse-{coarse}.png"
            rotate_stamp_image(color, path, coarse)
            image = read_cv(path)
            boxes = detect_orientation_boxes(path, scratch, model_variant=model_variant)
            for group in horizontal_groups(boxes, image.shape[1]):
                group.update(coarse=coarse, path=path)
                seeds.append(group)
        # A crowded/multi-row layout belongs to its own pipeline, not an
        # unbounded search of all combinations of unrelated text.
        if not seeds or len(seeds) > 8:
            return None
        for index, seed in enumerate(seeds):
            image = read_cv(seed["path"])
            h, w = image.shape[:2]
            matrix, fw, fh = _rotation_geometry(w, h, seed["angle"])
            points = np.concatenate([np.asarray(_transform_points(b["points"], matrix))
                                     for b in seed["boxes"]])
            x0, y0 = points.min(0)
            x1, y1 = points.max(0)
            height = y1 - y0
            reference = rotate_stamp_pixels(raw, seed['coarse'])
            b, g, r = cv2.split(reference.astype(float))
            # Continuous source colour evidence: never threshold faint strokes
            # out, and never reconstruct characters from the required string.
            difference = r - np.minimum(b, g) if ink_color == "red" else b - np.minimum(r, g)
            soft = np.clip(255 - np.maximum(difference, 0) * 3, 0, 255).astype(np.uint8)
            for mode, source in (("existing", image), (f"soft-{ink_color}", soft), ("raw", reference)):
                whole = rotate_stamp_pixels(source, seed['angle'])
                whole_path = scratch / f"whole-{index}-{mode}.png"
                write_temporary_input(whole_path, whole)
                for pad, (left, right, top, bottom) in enumerate(CONTEXTS):
                    box = [max(0, round(x0-left*height)), max(0, round(y0-top*height)),
                           min(fw, round(x1+right*height)), min(fh, round(y1+bottom*height))]
                    row = whole[box[1]:box[3], box[0]:box[2]]
                    row = cv2.copyMakeBorder(row, 16, 16, 16, 16, cv2.BORDER_CONSTANT,
                                             value=(255, 255, 255))
                    row_path = scratch / f"row-{index}-{mode}-{pad}.png"
                    write_temporary_input(row_path, row)
                    reads = reader.read_line(row_path, provider=provider)
                    trials.append(dict(seed=index, mode=mode, pad=pad, box=box,
                                       text="".join(x.text for x in reads),
                                       confidence=float(np.mean([x.confidence for x in reads])) if reads else 0.,
                                       path=row_path))
        winner = choose_consensus(trials)
        if winner is None:
            return None
        previous = (prior or {}).get("type_band_text") or ""
        # Repeated readings of a clipped substring are not evidence that a
        # previously verified prefix should disappear.
        if not preserves_verified_row(previous, winner["text"]):
            return None
        seed = seeds[winner["seed"]]
        clean = read_cv(scratch / f"whole-{winner['seed']}-existing.png")
        geometry = _fit_ellipse_annulus(_ocr_color_mask(clean, ink_color))
        if geometry is None:
            return None
        mx, my = _ellipse_normal_maps(*geometry)
        guide = SimpleNamespace(annulus=geometry, source_shape=clean.shape[:2], map_x=mx, map_y=my)
        mono = read_cv(scratch / f"whole-{winner['seed']}-soft-{ink_color}.png", 0)
        channels = [mono, mono, np.full_like(mono, 255)]
        working = cv2.merge(channels if ink_color == "red" else channels[::-1])
        quality = ring_evidence_quality(clean, working, guide)
        if not quality["accepted"]:
            return None
        center = destination.with_stem(destination.stem + "-row")
        copy_image(winner["path"], center)
        _write(destination, working)
    left, top, right, bottom = winner["mask_box"]
    orientation = {
        "mode": "combined", "model": "横排完整范围与多输入一致性验证",
        "coarse_rotation": seed["coarse"], "applied_rotation": seed["coarse"],
        "fine_rotation": seed["angle"], "rotation_steps": [a for a in (seed["coarse"], seed["angle"]) if a],
        "anchor_text": winner["text"], "type_band_text": winner["text"],
        "confidence": winner["confidence"], "type_band_confidence": winner["confidence"],
        "oriented_path": str(destination), "type_row_texts": [winner["text"]],
        "oriented_type_row_box": winner["box"], "oriented_type_row_read_box": winner["box"],
        "oriented_type_row_exact_box": winner["mask_box"],
        "oriented_type_row_polygons": [[[left, top], [right, top], [right, bottom], [left, bottom]]],
        "status": f"完整横排已验证：{winner['text']}；按横排方向旋正整章",
    }
    audit = {"method": "bounded_horizontal_consensus", "text": winner["text"],
             "ring_evidence_quality": quality,
             "support": winner["support"], "selected_mode": winner["mode"],
             "read_box": winner["box"], "mask_box": winner["mask_box"],
             "alternatives": winner["alternatives"],
             "candidates": [{k: v for k, v in row.items() if k != "path"} for row in trials]}
    return {"working": destination, "center": center, "orientation": orientation,
            "guide": guide, "audit": audit}


@timed_image('denoise', '去噪与干扰清理')
def protect_consensus_border(source, destination, mask_path, guide):
    """Restore the pre-mask border pixels; never refit a partly erased stamp."""
    image = read_cv(source)
    mask = read_cv(mask_path, 0)
    rim = np.zeros(image.shape[:2], np.uint8)
    cv2.ellipse(rim, guide.annulus[0][2], 255, max(5, round(min(image.shape[:2])*.03)))
    mask[rim > 0] = 0
    output = image.copy()
    output[mask > 0] = 255
    _write(destination, output)
    _write(mask_path, mask)
