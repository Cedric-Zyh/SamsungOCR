"""Separate one measured receiving stamp's identity from its value cells.

No requested company, station code or stamp number is accepted by this module.
Only actual observations from the same physical row may support an identity.
"""
from __future__ import annotations

import math
import re

MIN_CONFIDENCE = .75
_PURPOSE = "收货专用章"
_NUMBERED_PURPOSE = re.compile(r"^收货专用章([①-⑳]|[（(][0-9]{1,3}[)）])?$")
_CAPTION = re.compile(r"^(?:收货人|收货日期|收货时间|签收人|签收日期|日期)")


def normalize_receiving_text(text):
    """Ignore harmless width/spacing, preserving every code and numeric glyph."""
    return "".join(
        chr(ord(ch) - 0xFEE0) if "\uff01" <= ch <= "\uff5e" else ch
        for ch in str(text or "")
        if not ch.isspace() and ch not in "()（）|｜"
    )


def observation_confidence(value):
    if isinstance(value, bool):
        return 0.0
    try:
        score = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return score if math.isfinite(score) and 0 <= score <= 1 else 0.0


def _compatible(left, right):
    if normalize_receiving_text(left) == normalize_receiving_text(right):
        return True
    # An actual numbered purpose can complete the same purpose whose separate
    # side number the detector omitted. Different observed numbers still clash.
    left, right = (re.sub(r"\s+", "", str(text)) for text in (left, right))
    return bool(_NUMBERED_PURPOSE.fullmatch(left) and _NUMBERED_PURPOSE.fullmatch(right)
                and (left == _PURPOSE or right == _PURPOSE))


def _row_candidates(row):
    values = []
    if row.get("text") is not None:
        values.append({"text": str(row.get("text") or ""),
                       "confidence": observation_confidence(row.get("confidence")),
                       "source": row.get("source", "primary")})
    for value in row.get("variants") or []:
        if isinstance(value, dict):
            values.append({"text": str(value.get("text") or ""),
                           "confidence": observation_confidence(value.get("confidence")),
                           "source": value.get("source", value.get("name", "variant"))})
    return [v for v in values if v["text"].strip() and v["confidence"] >= MIN_CONFIDENCE]


def _select_row(values):
    conflicts = [{"a": a, "b": b} for position, a in enumerate(values)
                 for b in values[position + 1:] if not _compatible(a["text"], b["text"])]
    if conflicts or not values:
        return "", conflicts
    selected = max(values, key=lambda value: (len(normalize_receiving_text(value["text"])),
                                               value["confidence"]))
    return selected["text"], []


def _empty_decision():
    return {"identity_text": "", "can_compare": False, "identity_rows": [],
            "form_rows": [], "conflicts": [], "warnings": [], "purpose_row_index": None}


def decide_receiving_identity(rows, *, detected_boxes=None, grid_verified=False):
    """Build one internally consistent identity without consulting a target.

    ``rows`` contains physical top-to-bottom cells with integer ``row_index``,
    ``text``, ``confidence`` and optional complete-row ``variants``. A skipped
    separator needs both ``is_blank=True`` and a positive pixel ``blank_check``.
    Unread or low-confidence text alone is never evidence of an empty cell.

    Independent boxes must already be mapped to the same physical row with
    :func:`map_receiving_detections`; unmapped boxes cannot be stitched here.
    ``can_compare`` only makes text available to the ordinary strict matcher;
    it does not authorize final business acceptance.
    """
    result = _empty_decision()
    physical = []
    for position, row in enumerate(rows or []):
        if not isinstance(row, dict):
            result["conflicts"].append({"reason": "invalid_physical_row"})
            return result
        index = row.get("row_index", row.get("index", position))
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            result["conflicts"].append({"reason": "invalid_physical_row_index"})
            return result
        physical.append(dict(row, row_index=index))
    physical.sort(key=lambda row: row["row_index"])
    if len({row["row_index"] for row in physical}) != len(physical):
        result["conflicts"].append({"reason": "duplicate_physical_row_index"})
        return result
    detections = {}
    indexes = {row["row_index"] for row in physical}
    for box in detected_boxes or []:
        index = box.get("row_index")
        if isinstance(index, int) and not isinstance(index, bool) and index in indexes:
            detections.setdefault(index, []).append(box)
        else:
            result["warnings"].append("unmapped_detector_not_used")
    values_by_row = {}
    for row in physical:
        index = row["row_index"]
        values = _row_candidates(row)
        boxes = [b for b in detections.get(index, [])
                 if observation_confidence(b.get("confidence")) >= MIN_CONFIDENCE
                 and str(b.get("text") or "").strip()]
        if boxes:
            boxes.sort(key=lambda box: _finite_coordinate(box.get("left", box.get("x", 0))))
            values.append({"text": "".join(str(b["text"]) for b in boxes),
                           "confidence": min(observation_confidence(b.get("confidence")) for b in boxes),
                           "source": "detector_same_physical_row"})
        values_by_row[index] = values
    purposes = [r["row_index"] for r in physical
                if any(_PURPOSE in normalize_receiving_text(v["text"])
                       for v in values_by_row[r["row_index"]])]
    if len(purposes) != 1:
        result["warnings"].append("no_observed_purpose" if not purposes else "multiple_purpose_rows")
        return result
    purpose = purposes[0]
    result["purpose_row_index"] = purpose
    after = [row for row in physical if row["row_index"] > purpose]
    if grid_verified is not True:
        result["warnings"].append("physical_grid_not_verified")
        result["form_rows"] = [row for row in after if any(
            _CAPTION.match(normalize_receiving_text(v["text"])) for v in values_by_row[row["row_index"]])]
        return result
    result["form_rows"] = after
    for row in [r for r in physical if r["row_index"] <= purpose]:
        index = row["row_index"]
        values = values_by_row[index]
        if not values:
            if row.get("is_blank") is True and (row.get("blank_check") or {}).get("blank") is True:
                continue
            result["conflicts"].append({"row_index": index, "reason": "unread_nonblank_or_unknown_row"})
            continue
        text, conflicts = _select_row(values)
        if conflicts:
            result["conflicts"].append({"row_index": index,
                                        "reason": "disagreeing_whole_row_observations",
                                        "observations": values, "pairs": conflicts})
        else:
            result["identity_rows"].append({"row_index": index, "text": text, "observations": values})
    subject = [row for row in result["identity_rows"] if row["row_index"] < purpose]
    if not subject or sum("\u4e00" <= ch <= "\u9fff" for row in subject for ch in row["text"]) < 2:
        result["warnings"].append("missing_observed_subject")
        return result
    if any(_CAPTION.match(normalize_receiving_text(row["text"])) for row in subject):
        result["conflicts"].append({"reason": "form_caption_before_purpose"})
    result["identity_text"] = "".join(row["text"] for row in result["identity_rows"])
    result["can_compare"] = not result["conflicts"]
    return result


def _finite_coordinate(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


# Keep the pipeline's public boundary in one module; the independent policy
# module imports these pure helpers only when its functions execute.
from .receiving_policy import (  # noqa: E402
    choose_receiving_identity,
    map_receiving_detections,
    older_identity_observations,
    verified_grid_alternatives,
    verify_blank_row,
)
