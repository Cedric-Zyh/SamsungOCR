"""Stamp orientation: combined orientation."""

from pathlib import Path
import math
import tempfile
import numpy as np
from PIL import Image
from receipt_ocr.recognition.seal.preprocess.orientation_settings import (
    FOUR_WAY_ANCHOR_MIN_CONFIDENCE, FOUR_WAY_COARSE_ANGLES,
    FOUR_WAY_MIN_FINE_ROTATION, FOUR_WAY_MAX_FINE_ROTATION,
    FOUR_WAY_FINAL_ROW_MIN_CONFIDENCE,
)
from receipt_ocr.recognition.seal.preprocess.orientation_angles import (
    _round_stamp_type_texts, _stamp_type_marker_strength, choose_round_stamp_angle,
)
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _map_box_to_source, _oriented_type_anchor_box, _oriented_type_row_exact_box, _oriented_type_row_polygons, _rotation_geometry, _transform_points, rotate_stamp_image
from .horizontal_rows import grouped_type_candidate, polygon_bounds
from .row_extent import verify_row_extent
from .detection_scale import detect_orientation_boxes


def _orient_complete_row(source, best, scratch, band_dir, *, model_variant):
    """Verify a full row, rotate the whole stamp, then map the same polygons.

    Search images are temporary. Render the final fine rotation from the
    coarse image once, without accumulating a second fine interpolation.
    """
    from ..ocr.interface import OcrReader

    if best.get("grouped_row"):
        return best
    seed = _oriented_type_row_exact_box(
        best["boxes"], best["decision"], best["fine_matrix"],
        best["final_width"], best["final_height"],
    )
    if seed is None:
        return best
    proposal = verify_row_extent(
        best["path"], Path(band_dir) / f"coarse-{int(best['angle'])}-complete-row-before.png",
        seed, best["band_text"], best["band_confidence"],
        reader=OcrReader(), provider=f"paddle_{model_variant}",
    )
    if proposal is None:
        return best
    residual = float(proposal["baseline_angle"])
    fine = best["fine_rotation"] + residual
    if abs(fine) > FOUR_WAY_MAX_FINE_ROTATION:
        return best
    coarse = int(best["angle"])
    coarse_source = Path(source) if coarse == 0 else Path(scratch) / f"coarse-{coarse}.png"
    final_path = Path(scratch) / "complete-row-oriented.png"
    rotate_stamp_image(coarse_source, final_path, fine)
    matrix, width, height = _rotation_geometry(
        best["candidate_width"], best["candidate_height"], fine,
    )
    previous = np.vstack((best["fine_matrix"], [0, 0, 1]))
    mapping = (np.vstack((matrix, [0, 0, 1])) @ np.linalg.inv(previous))[:2]
    polygon = _transform_points(proposal["polygon"], mapping)
    interior = _transform_points(proposal["protected_interior"], mapping)
    exact = polygon_bounds([polygon], width, height)
    focus = polygon_bounds([polygon], width, height, .025)
    # Read directly from the newly rotated WHOLE stamp, without a row-only warp.
    text, confidence = _read_type_band_text(
        final_path, Path(band_dir) / f"coarse-{coarse}-complete-row.png",
        model_variant=model_variant, focus_box=focus,
    )
    if text != proposal["text"] or not math.isfinite(confidence) or confidence < .9:
        return best
    proposal.update(
        before_rotation_polygon=proposal["polygon"], polygon=polygon,
        protected_interior=interior, correction_angle=residual,
        text_before_rotation=proposal["text"], text=text, confidence=confidence,
        coordinate_frame="final_oriented_stamp",
    )
    return dict(
        best, path=final_path, fine_rotation=fine, fine_matrix=matrix,
        final_width=width, final_height=height, focus_box=focus, exact_box=exact,
        mask_polygons=[polygon], band_text=text, band_confidence=confidence,
        complete_row=proposal,
    )


def _read_type_band_text(image_path, band_path, *, model_variant, focus_box=None):
    """Recognise the stamp-type row of one candidate.

    ``focus_box`` comes from observed text. Without one there is no row to
    read; a fixed centre strip could read ring lettering instead.
    """
    from receipt_ocr.recognition.seal.ocr.interface import read_line

    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        if focus_box and len(focus_box) == 4:
            left = max(0, min(width, round(float(focus_box[0]))))
            top = max(0, min(height, round(float(focus_box[1]))))
            right = max(0, min(width, round(float(focus_box[2]))))
            bottom = max(0, min(height, round(float(focus_box[3]))))
        else:
            return "", 0.0
        if right - left < 8 or bottom - top < 8:
            return "", 0.0
        band_path = Path(band_path)
        band_path.parent.mkdir(parents=True, exist_ok=True)
        rgb.crop((left, top, right, bottom)).save(band_path)
    rows = [
        row for row in read_line(band_path, model_variant=model_variant)
        if getattr(row, "text", "")
    ]
    if not rows:
        return "", 0.0
    text = "".join(str(row.text) for row in rows)
    confidence = sum(float(getattr(row, "confidence", 0.0) or 0.0) for row in rows) / len(rows)
    return text, confidence

def _four_way_candidate(source, scratch, band_dir, angle, *, model_variant, observed_boxes=None):
    """Coarse-rotate to ``angle``, fine-tune on the type polygon, read the row.

    ``scratch`` holds the rotated canvases, which are working state only; the
    four band crops (the actual decision evidence) go to ``band_dir`` so a
    reviewer can see why one angle won without keeping four full-size crops
    per seal.
    """
    scratch, band_dir = Path(scratch), Path(band_dir)
    candidate_path = Path(source) if not angle else scratch / f"coarse-{angle}.png"
    if angle:
        rotate_stamp_image(source, candidate_path, angle)
    with Image.open(candidate_path) as image:
        candidate_width, candidate_height = image.size

    boxes = []
    anchor_text = ""
    anchor_confidence = 0.0
    anchor_angle = 0.0
    anchor_points = []
    error = ""
    try:
        boxes = (detect_orientation_boxes(candidate_path, scratch, model_variant=model_variant)
                 if observed_boxes is None else observed_boxes)
    except Exception as exc:  # a broken angle must not abort the comparison
        error = str(exc)
    try:
        grouped = grouped_type_candidate(
            candidate_path, boxes, scratch, band_dir, angle,
            model_variant=model_variant, read_row=_read_type_band_text,
        )
    except Exception:
        grouped = None  # A failed optional group must not discard a valid single row.
    decision = choose_round_stamp_angle(
        boxes, minimum_confidence=FOUR_WAY_ANCHOR_MIN_CONFIDENCE
    )
    anchor_text = str(decision.get("anchor_text", "") or "")
    anchor_confidence = float(decision.get("confidence", 0.0) or 0.0)
    anchor_points = decision.get("anchor_points") or []
    fine_rotation = float(decision.get("angle") or 0.0) if anchor_text else 0.0
    if not math.isfinite(fine_rotation) or abs(fine_rotation) <= FOUR_WAY_MIN_FINE_ROTATION:
        fine_rotation = 0.0

    identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    final_path = candidate_path
    fine_matrix = identity
    final_width, final_height = candidate_width, candidate_height
    if fine_rotation:
        fine_path = scratch / f"coarse-{angle}-fine.png"
        rotate_stamp_image(candidate_path, fine_path, fine_rotation)
        final_path = fine_path
        with Image.open(final_path) as image:
            final_width, final_height = image.size
        fine_matrix, _, _ = _rotation_geometry(
            candidate_width, candidate_height, fine_rotation
        )
    focus_box = _oriented_type_anchor_box(
        decision, fine_matrix, final_width, final_height
    )
    band_text, band_confidence = _read_type_band_text(
        final_path,
        band_dir / f"coarse-{angle}-type-row.png",
        model_variant=model_variant,
        focus_box=focus_box,
    )
    single = {
        "angle": float(angle),
        "path": final_path,
        "fine_rotation": fine_rotation,
        "fine_matrix": fine_matrix,
        "candidate_width": candidate_width,
        "candidate_height": candidate_height,
        "final_width": final_width,
        "final_height": final_height,
        "focus_box": focus_box,
        "anchor_text": anchor_text,
        "anchor_confidence": anchor_confidence,
        "anchor_angle": float(decision.get("angle") or 0.0) if anchor_text else 0.0,
        "anchor_points": anchor_points,
        "boxes": boxes,
        "decision": decision,
        "band_text": band_text,
        "band_confidence": band_confidence,
        "error": error,
    }
    if grouped and (len(grouped["band_text"]) > len(band_text)
                    or abs(fine_rotation) > FOUR_WAY_MAX_FINE_ROTATION
                    or _stamp_type_marker_strength(band_text) < 2):
        return grouped
    return single

def _select_four_way_candidate(candidates):
    """Select from the actual post-rotation row, never the earlier anchor."""
    verified = []
    for item in candidates:
        text, confidence = item["band_text"], item["band_confidence"]
        strength = _stamp_type_marker_strength(text)
        fine = item["fine_rotation"]
        reason = ""
        if item["error"]:
            reason = "候选识别失败"
        elif not math.isfinite(fine) or abs(fine) > FOUR_WAY_MAX_FINE_ROTATION:
            # Four coarse directions cover every quarter-turn already. A
            # larger fine correction can flip an otherwise upright stamp.
            reason = "微调角度超过 45°"
        elif (strength < 2 or len(text) < 3 or not math.isfinite(confidence)
              or confidence < FOUR_WAY_FINAL_ROW_MIN_CONFIDENCE):
            reason = "转正后未读到可靠章型行"
        item["post_rotation_verified"] = not reason
        item["rejection_reason"] = reason
        item["rank"] = (
            ((strength, len(text)), 1, round(confidence, 4))
            if not reason else ((0, 0), 0, 0.0)
        )
        if not reason:
            verified.append(item)
    if not verified:
        return next(item for item in candidates if item["angle"] == 0)
    best = max(verified, key=lambda item: (item["rank"], -abs(item["fine_rotation"])))
    # Equivalent readings prefer the least resampling, then the original view.
    equivalent = [item for item in verified
                  if item["band_text"] == best["band_text"]
                  and item["band_confidence"] >= best["band_confidence"] - 0.03]
    return min(equivalent, key=lambda item: (abs(item["fine_rotation"]), item["angle"])) if equivalent else best


def _rebase_large_angle(source, scratch, band_dir, item, *, model_variant):
    """Reuse an observed polygon in a new coarse frame, then re-read pixels.

    A nearly vertical detected row need not be detectable again at the next
    right angle. Transform its polygon instead; its old OCR text is only a
    locator, never evidence that the newly rendered final row is readable.
    """
    fine = float(item["fine_rotation"])
    if (not math.isfinite(fine) or not 45 < abs(fine) <= 90
            or _stamp_type_marker_strength(item["band_text"]) < 2
            or not math.isfinite(item["band_confidence"])
            or item["band_confidence"] < FOUR_WAY_FINAL_ROW_MIN_CONFIDENCE
            or not item.get("boxes")):
        return None
    old_angle = float(item["angle"])
    shift = 90 if fine > 0 else -90
    new_angle = (old_angle + shift) % 360
    with Image.open(source) as image:
        width, height = image.size
    old_matrix, _, _ = _rotation_geometry(width, height, old_angle)
    new_matrix, _, _ = _rotation_geometry(width, height, new_angle)
    mapping = (np.vstack((new_matrix, [0, 0, 1])) @
               np.linalg.inv(np.vstack((old_matrix, [0, 0, 1]))))[:2]
    boxes = []
    for box in item["boxes"]:
        mapped = dict(box)
        mapped["points"] = _transform_points(box.get("points") or [], mapping)
        if len(mapped["points"]) < 4:
            continue
        mapped["angle"] = (float(box.get("angle", 0)) - shift + 90) % 180 - 90
        boxes.append(mapped)
    trial_dir = Path(scratch) / f"rebase-{old_angle:g}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = Path(band_dir) / f"rebase-{old_angle:g}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    trial = _four_way_candidate(source, trial_dir, evidence_dir, new_angle,
                                model_variant=model_variant, observed_boxes=boxes)
    trial["angle_rebase"] = {"from_coarse": old_angle, "from_fine": fine,
                             "to_coarse": new_angle, "to_fine": trial["fine_rotation"]}
    return trial


def prepare_round_stamp_combined(source, destination, *, model_variant="v6", retry_large_angles=False):
    """Find the observed type row at right angles, then diagonals if needed.

    A text polygon cannot resolve the quarter-turn: the detector normalises
    every line angle to ``[-90, 90)``, so its geometry only says whether a row
    is level, never which way up the stamp is.  The coarse quarter-turn is
    therefore chosen by evidence: each candidate is rotated and its observed
    type row is read. Split fragments are verified as a whole row before a
    small correction; four failed directions trigger intermediate angles.

    The polygon locates the type row, including rows below the star. Selection
    then requires a readable type marker in the final rotated crop. A good
    pre-rotation detection cannot rescue a failed final row or a fine rotation
    beyond 45 degrees. With ``retry_large_angles``, an otherwise readable
    large-angle row may be transferred to a new coarse frame and re-read;
    that new candidate must satisfy the same small-angle verification.

    The comparison never consults the requested seal text.  It rewards only
    the generic ``收货/专用/用章…章`` markers that every receipt stamp carries,
    so a wrong angle cannot be selected by echoing the answer.  When no angle
    produces a plausible row the original orientation is kept unchanged.
    """
    destination = Path(destination)
    # One evidence subdirectory per crop: the caller may process several
    # stamps into the same artifact folder, and ``candidate-90-type-row.png``
    # must never be shared.
    band_dir = destination.parent / f"{destination.stem}-fourway"
    band_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    with Image.open(source) as image:
        source_width, source_height = image.size
    candidates = []
    with tempfile.TemporaryDirectory(prefix="receipt-four-way-") as scratch:
        for angle in FOUR_WAY_COARSE_ANGLES:
            try:
                candidates.append(
                    _four_way_candidate(
                        source, scratch, band_dir, angle, model_variant=model_variant
                    )
                )
            except Exception as exc:
                candidates.append({
                    "angle": float(angle),
                    "path": source,
                    "fine_rotation": 0.0,
                    "fine_matrix": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
                    "candidate_width": source_width,
                    "candidate_height": source_height,
                    "final_width": source_width,
                    "final_height": source_height,
                    "focus_box": None,
                    "anchor_text": "",
                    "anchor_confidence": 0.0,
                    "anchor_angle": 0.0,
                    "anchor_points": [],
                    "boxes": [],
                    "decision": {},
                    "band_text": "",
                    "band_confidence": 0.0,
                    "rank": ((0, 0, 0), 0, 0.0),
                    "error": str(exc),
                })

        best = _select_four_way_candidate(candidates)
        if not best["post_rotation_verified"]:
            # A diagonal row can be missed at all four right angles. Only
            # then compare the intermediate angles on the same full stamp.
            for angle in (45, 135, 225, 315):
                try:
                    candidates.append(_four_way_candidate(
                        source, scratch, band_dir, angle, model_variant=model_variant,
                    ))
                except Exception:
                    continue
            best = _select_four_way_candidate(candidates)

        if retry_large_angles and not best["post_rotation_verified"]:
            # Only retry otherwise readable large-angle rows. The ordinary
            # post-rotation text, confidence and <=45-degree checks still apply.
            for item in list(candidates):
                try:
                    trial = _rebase_large_angle(source, scratch, band_dir, item,
                                               model_variant=model_variant)
                    if trial is not None:
                        candidates.append(trial)
                except Exception:
                    continue
            best = _select_four_way_candidate(candidates)

        decision = {
            "mode": "combined",
            "model": "整章横排文字检测 + 按需补试斜向角度",
            "applied_rotation": 0.0,
            "coarse_rotation": 0.0,
            "fine_rotation": 0.0,
            "anchor_text": "",
            "confidence": 0.0,
            "oriented_path": "",
        }
        # Prefer the unrotated view when two angles read equally well; a
        # needless quarter-turn changes the secondary read image for no gain.
        decision["four_way_candidates"] = {
            (f"rebase-{item['angle_rebase']['from_coarse']:g}"
             if item.get("angle_rebase") else str(int(item["angle"]))): {
                "text": item["band_text"],
                "fine_rotation": round(item["fine_rotation"], 3),
                "anchor_text": item["anchor_text"],
                "confidence": round(item["band_confidence"], 4),
                "post_rotation_verified": item["post_rotation_verified"],
                "rejection_reason": item["rejection_reason"],
                "rank": list(item["rank"]),
                "error": item["error"],
                **({"angle_rebase": item["angle_rebase"]} if item.get("angle_rebase") else {}),
            }
            for item in candidates
        }
        if best["error"]:
            decision["error"] = best["error"]
        if not best["post_rotation_verified"]:
            decision["status"] = "各候选方向均未找到章型文字的可靠转正结果，保留原方向"
            return None, decision

        if best.get("angle_rebase"):
            decision["angle_rebase"] = best["angle_rebase"]
        try:
            best = _orient_complete_row(
                source, best, scratch, band_dir, model_variant=model_variant,
            )
        except Exception as exc:
            # A failed refinement must not discard a verified coarse candidate.
            decision["complete_row_error"] = str(exc)
        coarse_angle = float(best["angle"])
        is_original = coarse_angle == 0.0 and not best["fine_rotation"]
        if is_original:
            # The untouched crop already carries the best reading.  Keep its
            # pixels exactly and report no rotation so every downstream stage
            # falls back to the original colour-isolated image.
            oriented = None
        else:
            from shutil import copyfile

            copyfile(Path(best["path"]), destination)
            oriented = destination
            decision["oriented_path"] = str(destination)

        # Retain the actual winning OCR pixels before the scratch canvas is
        # removed. A later cleaned crop must not discard a more complete read.
        if best["focus_box"]:
            verified_row = destination.with_name(destination.stem + "-verified-type-row.png")
            with Image.open(best["path"]) as image:
                image.convert("RGB").crop(tuple(round(v) for v in best["focus_box"])).save(verified_row)
            decision["type_band_path"] = str(verified_row)

    decision.update(
        applied_rotation=coarse_angle,
        coarse_rotation=coarse_angle,
        fine_rotation=round(best["fine_rotation"], 3),
        rotation_steps=[angle for angle in (coarse_angle, best["fine_rotation"]) if angle],
        angle=coarse_angle,
        anchor_text=best["anchor_text"],
        anchor_angle=round(best["anchor_angle"], 3),
        confidence=round(best["anchor_confidence"], 4),
        type_band_text=best["band_text"],
        type_band_confidence=round(best["band_confidence"], 4),
        # Only the winning row geometry is persisted, not every detection at
        # each trial angle.
        type_row_texts=_round_stamp_type_texts(best["boxes"], best["decision"]),
    )
    if best["focus_box"]:
        decision["oriented_type_row_box"] = best["focus_box"]
        # Reading context and the exact ink mask serve different purposes.
        decision["oriented_type_row_read_box"] = best["focus_box"]
        decision["oriented_type_row_exact_box"] = best.get("exact_box") or _oriented_type_row_exact_box(
            best["boxes"], best["decision"], best["fine_matrix"],
            best["final_width"], best["final_height"],
        )
        decision["oriented_type_row_polygons"] = best.get("mask_polygons") or _oriented_type_row_polygons(
            best["boxes"], best["decision"], best["fine_matrix"],
        )
        if best.get("complete_row"):
            decision["horizontal_row_localization"] = best["complete_row"]
            decision["oriented_type_row_read_box"] = best["focus_box"]
            decision["type_row_texts"] = [best["band_text"]]
        elif best.get("grouped_row"):
            decision["type_row_grouped"] = True
            decision["local_mask_verified"] = best["local_mask_verified"]
            decision["oriented_type_row_read_box"] = best["focus_box"]
            decision["type_row_texts"] = [best["band_text"]]
        decision["type_row_box"] = _map_box_to_source(
            best["focus_box"],
            best,
            coarse_angle,
            (source_width, source_height),
        )
    rotation_note = f"按 {coarse_angle:g}° 粗校正"
    if is_original:
        rotation_note = "原方向最佳"
    fine_note = (
        f"，再按章型文字微调 {best['fine_rotation']:.1f}°"
        if best["fine_rotation"] else ""
    )
    decision["status"] = (
        f"横排文字择优：{rotation_note}{fine_note}"
        f"（章型行：{best['band_text'] or '无'}）"
    )
    return oriented, decision
