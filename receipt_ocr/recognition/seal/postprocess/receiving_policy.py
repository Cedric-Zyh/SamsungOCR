"""Geometric proof and guarded fallback for receiving-stamp identities."""
from __future__ import annotations

import math
from pathlib import Path


def _helpers():
    # Lazy import avoids a cycle while receiving_identity re-exports this API.
    from .receiving_identity import normalize_receiving_text, observation_confidence
    return normalize_receiving_text, observation_confidence


def _bounds(values):
    try:
        numbers = [float(v) for v in values]
    except (TypeError, ValueError, OverflowError):
        return None
    return numbers if len(numbers) == 4 and all(math.isfinite(v) for v in numbers) else None


def map_receiving_detections(artifact, *, minimum_vertical_overlap=.80):
    """Map a canonical detector box only when >=80% belongs to one real cell."""
    import numpy as np
    frame = artifact.get("rectangular_frame") or {}
    crop = _bounds(frame.get("crop_box") or [])
    if crop is None:
        return []
    left, top, right, bottom = crop
    width, height = right-left, bottom-top
    try:
        canonical = float(frame.get("canonical_width") or 960)
        padding = float(frame.get("padding", 32))
        overlap_limit = max(.80, float(minimum_vertical_overlap))
    except (TypeError, ValueError, OverflowError):
        return []
    if (not all(math.isfinite(v) for v in [canonical, padding, overlap_limit])
            or min(width, height, canonical) <= 0 or overlap_limit > 1):
        return []
    scaled_height = round(height*canonical/width)
    if scaled_height <= 0:
        return []
    out = []
    for detection_index, box in enumerate(frame.get("detected_boxes") or []):
        try:
            points = np.asarray(box.get("points") or [], float)
        except (TypeError, ValueError):
            continue
        if points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        ys = top+(points[:, 1]-padding)*height/scaled_height
        xs = left+(points[:, 0]-padding)*width/canonical
        if ys.max() <= ys.min() or xs.max() <= xs.min():
            continue
        horizontal = max(0, min(right, xs.max())-max(left, xs.min()))/(xs.max()-xs.min())
        if not left <= xs.mean() <= right or horizontal < .80:
            continue
        possible = []
        overlaps = {}
        for index, row in enumerate(artifact.get("rectangular_fixed_rows") or []):
            bounds = _bounds(row.get("crop_box") or [])
            if bounds is None or row.get("coordinate_space") != "oriented":
                continue
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            overlap = max(0, min(bounds[3], ys.max())-max(bounds[1], ys.min()))/(ys.max()-ys.min())
            if bounds[1] <= ys.mean() < bounds[3] and overlap >= overlap_limit:
                possible.append(index)
                overlaps[index] = float(overlap)
        if len(possible) == 1:
            index = possible[0]
            out.append(dict(box, row_index=index, left=float(xs.min()),
                            vertical_overlap=overlaps[index], detection_index=detection_index))
    return out


def verify_blank_row(image_or_path, *, known_padding=20, reference_row_height=None):
    """Verify a short separator from pixels, never from an empty OCR result.

    Red rules are removed only on a measurement copy. Dark neutral strokes,
    including a small handwritten digit over a red rule, must remain visible.
    """
    import cv2
    import numpy as np
    path = str(image_or_path) if isinstance(image_or_path, (str, Path)) else ""
    image = cv2.imread(path, cv2.IMREAD_COLOR) if path else image_or_path
    info = {"blank": False, "path": path, "method": "pixels_and_measured_separator"}
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        return dict(info, reason="unreadable_image")
    try:
        padding, reference = int(known_padding), float(reference_row_height)
    except (TypeError, ValueError, OverflowError):
        return dict(info, reason="invalid_padding_or_reference_height")
    if (padding < 0 or not math.isfinite(reference) or reference <= 0
            or min(image.shape[:2]) <= 2*padding or not np.isfinite(image).all()):
        return dict(info, reason="invalid_padding_or_reference_height")
    image = image[padding:image.shape[0]-padding, padding:image.shape[1]-padding] if padding else image
    height, width = image.shape[:2]
    info["size"] = [width, height]
    if height >= reference*.4:
        return dict(info, reason="not_a_subglyph_height_gap")
    pixels = image.astype(np.int16)
    colored = (pixels.max(2)-pixels.min(2) > 8).astype(np.uint8)
    neutral = (pixels.max(2) < 210).astype(np.uint8)
    profile = colored.mean(1)
    changes = np.diff(np.r_[False, profile > .10, False].astype(int))
    runs = zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
    bands = [(int(a), int(b)) for a, b in runs
             if profile[a:b].max() >= .65 and b-a <= height*.75]
    residual = colored.copy()
    for start, end in bands:
        residual[max(0, start-3):min(height, end+3)] = 0
    residual |= neutral
    _, _, stats, _ = cv2.connectedComponentsWithStats(residual, 8)
    meaningful = []
    for x, y, w, h, area in stats[1:]:
        edge = (x == 0 or x+w == width) and w <= 8 and h < height*.25 and area <= 25
        if not edge and h >= max(3, height*.15) and w >= 2 and area >= 8:
            meaningful.append([int(x), int(y), int(w), int(h), int(area)])
    density = float(residual.mean())
    blank = bool(not meaningful and density <= .006 and (bands or colored.mean() < .006))
    return dict(info, blank=blank, rule_bands=bands, residual_ink_pixels=int(residual.sum()),
                residual_density=density, neutral_dark_pixels=int(neutral.sum()),
                meaningful_components=meaningful,
                reason="verified_separator_or_empty_pixels" if blank else "residual_ink_or_no_separator_evidence")


def verified_grid_alternatives(records):
    """Require accepted geometry, one direction and two explicit agreeing views.

    Each record is one physical stamp with ``accepted=True`` and ``directions``.
    A direction contains physical ``rows`` and the result of
    ``decide_receiving_identity`` under ``decision``. Each row has color/gray
    variants with actual text and confidence; silence in either view fails.
    """
    normalize, confidence = _helpers()
    out = []
    for record in records or []:
        if record.get("accepted") is not True:
            continue
        eligible = []
        for direction in record.get("directions") or []:
            decision = direction.get("decision") or {}
            if decision.get("can_compare") is not True or not normalize(decision.get("identity_text")):
                continue
            rotation = direction.get("direction")
            if isinstance(rotation, bool) or rotation not in {0, 180}:
                continue
            physical = direction.get("rows") or []
            rows = {row.get("row_index"): row for row in physical}
            identity_rows = decision.get("identity_rows") or []
            if (len(rows) != len(physical)
                    or normalize("".join(r.get("text", "") for r in identity_rows))
                    != normalize(decision["identity_text"])):
                continue
            agrees = bool(decision.get("identity_rows"))
            for identity_row in identity_rows:
                views = rows.get(identity_row["row_index"], {}).get("variants") or []
                variants = {v.get("source"): v for v in views}
                color, gray = variants.get("color", {}), variants.get("gray", {})
                color_text, gray_text = normalize(color.get("text")), normalize(gray.get("text"))
                if (not color_text or not gray_text
                        or any(sum(v.get("source") == source for v in views) != 1 for source in ["color", "gray"])
                        or min(confidence(color.get("confidence")), confidence(gray.get("confidence"))) < .75
                        or color_text != gray_text or color_text != normalize(identity_row.get("text"))):
                    agrees = False
                    break
            if agrees:
                eligible.append(dict(decision, grid_verified=True, dual_representation_verified=True,
                                     direction=direction.get("direction")))
        if len(eligible) == 1:
            out.extend(eligible)
    return out


def choose_receiving_identity(primary, physical_rows, alternatives, *, older_observations=None):
    """Preserve useful primary evidence; recover insufficiency, not conflicts."""
    normalize, confidence = _helpers()
    primary = primary or {}
    if primary.get("can_compare") is True and normalize(primary.get("identity_text")):
        return {"text": primary["identity_text"], "can_compare": True, "source": "primary",
                "reason": "usable_primary_retained"}
    if any(c.get("reason") != "unread_nonblank_or_unknown_row" for c in primary.get("conflicts") or []):
        return _blocked("primary_meaningful_conflict")
    usable = [a for a in alternatives or [] if a.get("can_compare") is True
              and a.get("grid_verified") is True and a.get("dual_representation_verified") is True
              and normalize(a.get("identity_text"))]
    if len({normalize(a["identity_text"]) for a in usable}) != 1:
        return _blocked("no_unique_verified_grid_identity")
    text = usable[0]["identity_text"]
    normalized = normalize(text)
    purpose = primary.get("purpose_row_index")
    if purpose is None and any(confidence(r.get("confidence")) >= .75 and r.get("text") for r in physical_rows or []):
        return _blocked("unanchored_reliable_primary_not_overwritten")
    if purpose is not None:
        for row in physical_rows or []:
            if row["row_index"] > purpose:
                continue
            for value in [row, *(row.get("variants") or [])]:
                observed = normalize(value.get("text"))
                if confidence(value.get("confidence")) >= .75 and observed and observed not in normalized:
                    return _blocked("reliable_fragment_conflict", value.get("text"))
    for value in older_observations or []:
        observed = normalize(value.get("text"))
        if confidence(value.get("confidence")) >= .75 and observed and observed not in normalized:
            return _blocked("older_reliable_fragment_conflict", value.get("text"))
    return {"text": text, "can_compare": True, "source": "verified_grid_recovery",
            "reason": "insufficient_primary_recovered_without_character_replacement"}


def _blocked(reason, observed=None):
    result = {"text": "", "can_compare": False, "source": None, "reason": reason}
    if observed is not None:
        result["observed"] = observed
    return result


def older_identity_observations(artifact):
    """Retain old reliable subject/purpose ink, excluding observed value cells."""
    normalize, confidence = _helpers()
    reads = artifact.get("reads") or []
    indexes = [r["rectangle_row_index"] for r in reads
               if isinstance(r.get("rectangle_row_index"), int)
               and "收货专用章" in normalize(r.get("text")) and confidence(r.get("confidence")) >= .75]
    if len(set(indexes)) == 1:
        out = [r for r in reads if isinstance(r.get("rectangle_row_index"), int)
               and r["rectangle_row_index"] <= indexes[0]]
    else:
        out = [r for r in reads if r.get("channel") == "center"
               and "收货专用章" in normalize(r.get("text"))]
    for container in [artifact.get("rectangular_frame") or {}, artifact.get("orientation") or {}]:
        boxes = container.get("detected_boxes") or []
        purposes = [b for b in boxes if "收货专用章" in normalize(b.get("text"))
                    and confidence(b.get("confidence")) >= .75 and _finite_points(b.get("points"))]
        if len(purposes) != 1:
            continue
        ys = [float(p[1]) for p in purposes[0]["points"]]
        boundary = sum(ys)/4+(max(ys)-min(ys))*.2
        out.extend(b for b in boxes if _finite_points(b.get("points"))
                   and sum(float(p[1]) for p in b["points"])/4 <= boundary)
    return out


def _finite_points(points):
    try:
        return (len(points) == 4 and all(len(p) == 2 for p in points)
                and all(math.isfinite(float(value)) for p in points for value in p)
                and max(float(p[0]) for p in points) > min(float(p[0]) for p in points)
                and max(float(p[1]) for p in points) > min(float(p[1]) for p in points))
    except (TypeError, ValueError, OverflowError):
        return False
