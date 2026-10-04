"""Stamp orientation: angles."""

import math
import re
import numpy as np
from receipt_ocr.recognition.seal.preprocess.orientation_settings import MIN_CONFIDENCE, RECTANGULAR_MAX_SKEW, RECTANGULAR_TEXT_MIN_CONFIDENCE, ROUND_STAMP_ANCHOR_MIN_CONFIDENCE
from receipt_ocr.providers.orientation import MODEL_NAME


def choose_round_stamp_angle(boxes, *, minimum_confidence=ROUND_STAMP_ANCHOR_MIN_CONFIDENCE):
    """Choose a round-stamp rotation from a detected ``用章`` text polygon.

    The outer circle has no unique direction.  The fixed stamp-type suffix is
    the direction anchor instead.  Prefer the longest recognized suffix and
    accept partial readings such as ``货专用章``; never manufacture the missing
    prefix from the requested seal text.
    """
    candidates = []
    for box in boxes or []:
        text = str(box.get("text", "")).strip()
        confidence = float(box.get("confidence", 0.0) or 0.0)
        if confidence < minimum_confidence:
            continue
        # Round and oval receiving stamps often print the shorter centre row
        # ``收货章`` rather than ``收货专用章``.  It is still a valid direction
        # anchor; the row's text polygon is more reliable than the ellipse's
        # long axis because it also resolves the 180-degree ambiguity.
        is_stamp_type = (
            "用章" in text
            or "专用" in text
            or ("收货" in text and "章" in text)
        )
        if not is_stamp_type:
            continue
        try:
            angle = float(box.get("angle"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(angle):
            continue
        candidates.append((
            (1 if "用章" in text else 0, len(text), confidence),
            {
                "text": text,
                "confidence": confidence,
                "angle": angle,
                "points": box.get("points") or [],
            },
        ))
    if not candidates:
        # A detector can clip the suffix of a horizontal type row.  For
        # example, ``手机售后专`` is still a strong geometric anchor even
        # though it does not contain the complete ``专用章`` marker.  Accept
        # only high-confidence, multi-character service-type hints here; this
        # is a location/orientation fallback, never a completed OCR value.
        partial_candidates = []
        for box in boxes or []:
            text = str(box.get("text", "")).strip()
            confidence = float(box.get("confidence", 0.0) or 0.0)
            points = box.get("points") or []
            if confidence < minimum_confidence or len(text) < 3 or len(points) < 4:
                continue
            hint = (
                (text.endswith("专") and len(text) >= 4)
                or any(token in text for token in ("售后", "收货", "业务", "服务"))
            )
            if not hint:
                continue
            try:
                angle = float(box.get("angle"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(angle):
                continue
            partial_candidates.append((
                (len(text), confidence),
                {
                    "text": text,
                    "confidence": confidence,
                    "angle": angle,
                    "points": points,
                },
            ))
        if not partial_candidates:
            return {
                "status": "未找到印章类型方向锚点",
                "angle": None,
                "applied_rotation": 0.0,
                "anchor_text": "",
                "confidence": 0.0,
            }
        _, chosen = max(partial_candidates, key=lambda item: item[0])
        angle = chosen["angle"]
        applied = 0.0 if abs(angle) < 2.0 else angle
        return {
            "status": "找到横向文字候选（文本不完整）",
            "angle": round(angle, 3),
            "applied_rotation": round(applied, 3),
            "anchor_text": chosen["text"],
            "confidence": round(chosen["confidence"], 4),
            "anchor_points": chosen.get("points", []),
            "partial_anchor": True,
        }
    _, chosen = max(candidates, key=lambda item: item[0])
    angle = chosen["angle"]
    # A nearly horizontal line is already normalized.  Keep the measured value
    # in the evidence record, but avoid a needless interpolation pass.
    applied = 0.0 if abs(angle) < 2.0 else angle
    return {
        "status": "找到印章类型方向锚点",
        "angle": round(angle, 3),
        "applied_rotation": round(applied, 3),
        "anchor_text": chosen["text"],
        "confidence": round(chosen["confidence"], 4),
        "anchor_points": chosen.get("points", []),
    }

def choose_rectangular_stamp_angle(
    boxes,
    *,
    minimum_confidence=RECTANGULAR_TEXT_MIN_CONFIDENCE,
):
    """Choose a small deskew angle from a rectangular stamp text row.

    A rectangular service stamp often has only a company row and a numeric
    row, so the round-stamp ``专用章`` anchor is unavailable.  The widest
    horizontal OCR polygon is usually the large identifier row and provides a
    stable skew estimate without relying on the requested seal text.
    """
    candidates = []
    for box in boxes or []:
        text = str(box.get("text", "")).strip()
        confidence = float(box.get("confidence", 0.0) or 0.0)
        if not text or confidence < minimum_confidence:
            continue
        try:
            angle = float(box.get("angle"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(angle) or abs(angle) > RECTANGULAR_MAX_SKEW:
            continue
        points = box.get("points") or []
        if len(points) < 4:
            continue
        array = np.asarray(points, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != 2:
            continue
        span_x = float(array[:, 0].max() - array[:, 0].min())
        span_y = float(array[:, 1].max() - array[:, 1].min())
        if span_x < max(12.0, span_y * 1.25):
            continue
        candidates.append((
            (span_x, span_x / max(1.0, span_y), confidence),
            {"text": text, "confidence": confidence, "angle": angle, "points": points},
        ))
    if not candidates:
        return {
            "status": "未找到矩形章横向文字方向线",
            "angle": None,
            "applied_rotation": 0.0,
            "anchor_text": "",
            "confidence": 0.0,
        }
    _, chosen = max(candidates, key=lambda item: item[0])
    angle = chosen["angle"]
    applied = 0.0 if abs(angle) < 2.0 else angle
    return {
        "status": "找到矩形章横向文字方向线",
        "angle": round(angle, 3),
        "applied_rotation": round(applied, 3),
        "anchor_text": chosen["text"],
        "confidence": round(chosen["confidence"], 4),
        "anchor_points": chosen["points"],
    }

def _round_stamp_type_texts(boxes, decision):
    """Reuse detected type-row text, including a second numeric suffix line."""
    anchor_text = str(decision.get("anchor_text", "")).strip()
    if not anchor_text:
        return []
    try:
        anchor_angle = float(decision.get("angle", 0.0) or 0.0)
    except (TypeError, ValueError):
        anchor_angle = 0.0
    marker_pattern = re.compile(r"^[（(]?\s*\d{1,3}\s*[）)]?$")
    values = []
    for box in boxes or ():
        text = str(box.get("text", "")).strip()
        if text != anchor_text and not marker_pattern.fullmatch(text):
            continue
        try:
            angle = float(box.get("angle", anchor_angle))
        except (TypeError, ValueError):
            angle = anchor_angle
        delta = abs(angle - anchor_angle)
        delta = min(delta, abs(180.0 - delta))
        if text != anchor_text and delta > 15.0:
            continue
        if text not in values:
            values.append(text)
    if anchor_text in values:
        values.remove(anchor_text)
    return [anchor_text, *values]

def _stamp_type_marker_strength(text):
    """How strongly a reading looks like the closed-set stamp-type row.

    Deliberately generic: it never looks at the requested seal text, so the
    same signal is available for every candidate angle.
    """
    text = str(text or "")
    if "收货" in text and "章" in text:
        return 3
    if ("专用" in text and "章" in text) or "用章" in text:
        return 2
    if "章" in text:
        return 1
    return 0

def _reading_rank(text):
    """Rank one stamp-type reading: marker first, then how much was read."""
    text = str(text or "")
    strength = _stamp_type_marker_strength(text)
    return (1 if strength else 0, len(text), strength)

def _candidate_rank(anchor_text, band_text, mean_confidence, has_anchor):
    """Rank one coarse angle without consulting the requested seal text.

    Two independent readings describe the same row: the detector's own polygon
    text and the line recogniser's reading of that polygon's crop.  Whichever
    is stronger represents the angle, so a missing band crop cannot discard an
    angle whose row the detector already read.  Primary key is the presence of
    a stamp-type marker, so ring lettering can never beat a real type row; ties
    fall back to the reading length, then marker strength, the polygon anchor
    and confidence.
    """
    best = max(
        _reading_rank(anchor_text),
        _reading_rank(band_text),
    )
    return (
        best,
        1 if has_anchor else 0,
        round(float(mean_confidence or 0.0), 4),
    )

def decide_orientation(predictions):
    readings = []
    for prediction in predictions:
        labels, scores = prediction.get("label_names", []), prediction.get("scores", [])
        label = str(labels[0]) if len(labels) else ""
        angle = {"0_degree": 0, "180_degree": 180}.get(label)
        score = float(scores[0]) if len(scores) else 0.0
        readings.append({"angle": angle, "confidence": score if np.isfinite(score) else 0.0})
    decisive = bool(readings) and all(
        row["angle"] in (0, 180) and row["confidence"] >= MIN_CONFIDENCE for row in readings
    ) and len({row["angle"] for row in readings}) == 1
    return {
        "model": MODEL_NAME, "angle": readings[0]["angle"] if decisive else None,
        "confidence": min((row["confidence"] for row in readings), default=0.0),
        "line_predictions": readings, "applied_rotation": 0,
        "status": "方向已确认" if decisive else "方向不确定，保留原方向",
    }
