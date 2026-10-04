"""Stamp orientation: combined orientation."""

from pathlib import Path
import math
import tempfile
import numpy as np
from PIL import Image
from receipt_ocr.recognition.seal.preprocess.orientation_settings import FOUR_WAY_ANCHOR_MIN_CONFIDENCE, FOUR_WAY_BAND_BOTTOM_RATIO, FOUR_WAY_BAND_LEFT_RATIO, FOUR_WAY_BAND_RIGHT_RATIO, FOUR_WAY_BAND_TOP_RATIO, FOUR_WAY_COARSE_ANGLES, FOUR_WAY_MIN_FINE_ROTATION
from receipt_ocr.recognition.seal.preprocess.orientation_angles import _candidate_rank, _round_stamp_type_texts, choose_round_stamp_angle
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _map_box_to_source, _oriented_type_anchor_box, _oriented_type_row_exact_box, _rotation_geometry, rotate_stamp_image


def _read_type_band_text(image_path, band_path, *, model_variant, focus_box=None):
    """Recognise the stamp-type row of one candidate.

    ``focus_box`` is the polygon the detector actually read as the type row.
    A fixed horizontal strip is only a fallback: a round stamp may print its
    type row below the star rather than across the middle, so a centre strip
    would look at ring lettering instead of the row.
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
            left = round(width * FOUR_WAY_BAND_LEFT_RATIO)
            right = round(width * FOUR_WAY_BAND_RIGHT_RATIO)
            top = round(height * FOUR_WAY_BAND_TOP_RATIO)
            bottom = round(height * FOUR_WAY_BAND_BOTTOM_RATIO)
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

def _four_way_candidate(source, scratch, band_dir, angle, *, model_variant):
    """Coarse-rotate to ``angle``, fine-tune on the type polygon, read the row.

    ``scratch`` holds the rotated canvases, which are working state only; the
    four band crops (the actual decision evidence) go to ``band_dir`` so a
    reviewer can see why one angle won without keeping four full-size crops
    per seal.
    """
    from receipt_ocr.recognition.seal.ocr.interface import detect_boxes

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
        boxes = detect_boxes(candidate_path, model_variant=model_variant)
    except Exception as exc:  # a broken angle must not abort the comparison
        error = str(exc)
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
    return {
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
        "rank": _candidate_rank(
            anchor_text, band_text, band_confidence, bool(anchor_text)
        ),
        "error": error,
    }

def prepare_round_stamp_combined(source, destination, *, model_variant="v6"):
    """Coarse-rotate a round stamp to all four right angles, then fine-tune.

    A text polygon cannot resolve the quarter-turn: the detector normalises
    every line angle to ``[-90, 90)``, so its geometry only says whether a row
    is level, never which way up the stamp is.  The coarse quarter-turn is
    therefore chosen by evidence instead of by a model: each candidate is
    rotated, fine-tuned on its own stamp-type polygon, and its type row is
    read.  The angle whose row actually reads wins.

    Each candidate is scored from the detector's own polygon text and from the
    line reading of that polygon's crop, so an angle is never discarded just
    because its type row sits below the star rather than across the middle.

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

        decision = {
            "mode": "combined",
            "model": "四方向粗校正 + 印章文字检测框微调",
            "applied_rotation": 0.0,
            "coarse_rotation": 0.0,
            "fine_rotation": 0.0,
            "anchor_text": "",
            "confidence": 0.0,
            "oriented_path": "",
        }
        # Prefer the unrotated view when two angles read equally well; a
        # needless quarter-turn changes the secondary read image for no gain.
        best = max(candidates, key=lambda item: (item["rank"], -item["angle"]))
        decision["four_way_candidates"] = {
            str(int(item["angle"])): {
                "text": item["band_text"],
                "fine_rotation": round(item["fine_rotation"], 3),
                "anchor_text": item["anchor_text"],
                "rank": list(item["rank"]),
                "error": item["error"],
            }
            for item in candidates
        }
        if best["error"]:
            decision["error"] = best["error"]
        best_reading = best["rank"][0]
        # Refuse to rotate on a single stray glyph: require either a
        # stamp-type marker somewhere, or a real polygon anchor whose row reads
        # at least two characters.  Otherwise the original orientation stands.
        if not best_reading[0] and not (best["rank"][1] and best_reading[1] >= 2):
            decision["status"] = "四个方向均未找到章型文字，保留原方向"
            return None, decision

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

    decision.update(
        applied_rotation=coarse_angle,
        coarse_rotation=coarse_angle,
        fine_rotation=round(best["fine_rotation"], 3),
        angle=coarse_angle,
        anchor_text=best["anchor_text"],
        anchor_angle=round(best["anchor_angle"], 3),
        confidence=round(best["anchor_confidence"], 4),
        type_band_text=best["band_text"],
        type_band_confidence=round(best["band_confidence"], 4),
        # Only the type-row strings are persisted; the four full polygon sets
        # stay in memory because the artifact JSON is kept for every receipt.
        type_row_texts=_round_stamp_type_texts(best["boxes"], best["decision"]),
    )
    if best["focus_box"]:
        decision["oriented_type_row_box"] = best["focus_box"]
        decision["oriented_type_row_exact_box"] = _oriented_type_row_exact_box(
            best["boxes"], best["decision"], best["fine_matrix"],
            best["final_width"], best["final_height"],
        )
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
        f"四方向择优：{rotation_note}{fine_note}"
        f"（章型行：{best['band_text'] or '无'}）"
    )
    return oriented, decision
