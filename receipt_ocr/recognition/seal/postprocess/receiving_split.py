"""Concordant literal segment evidence, independent of requested seal text."""
import math
import re


def _literal(text):
    return re.sub(r"\s+", "", str(text or ""))


def _confidence(value):
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def _box(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(v, bool) for v in value):
        return None
    try:
        numbers = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if (not all(math.isfinite(v) for v in numbers)
            or not 0 <= numbers[0] < numbers[2] <= size[0]
            or not 0 <= numbers[1] < numbers[3] <= size[1]):
        return None
    return numbers


def decide_receiving_split(metadata, reads, *, whole_row_reads=None):
    """Return a candidate; reliable disagreement still requires review.

    ``reads`` uses the existing artifact contract: one actual observation per
    ``input_id`` with text and confidence. ``whole_row_reads`` must be the
    previous observations of this same physical title row, not form values.
    Omitting that evidence never grants automatic matching permission.
    """
    result = {"accepted": False, "title_text": "", "left": "", "code": "",
              "reason": "requires_verified_physical_row", "conflicts": [],
              "requires_review": True, "matching_allowed": False, "observations": []}
    if metadata.get("prepared") is not True or metadata.get("grid_verified") is not True:
        return result
    size = metadata.get("source_size")
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in size)):
        return dict(result, reason="invalid_source_dimensions")
    inputs = metadata.get("inputs") or []
    row_index = metadata.get("row_index")
    if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
        return dict(result, reason="invalid_physical_row_index")
    observations = {}
    input_ids = set()
    for item in inputs:
        input_id = item.get("id")
        if not isinstance(input_id, str) or not input_id or input_id in input_ids:
            return dict(result, reason="invalid_or_duplicate_input_id")
        input_ids.add(input_id)
        key = (item.get("representation"), item.get("split_part"), item.get("split_scale"))
        if isinstance(item.get("split_scale"), bool):
            return dict(result, reason="invalid_segment_scale")
        if key in observations:
            return dict(result, reason="duplicate_segment_input")
        if (item.get("rectangle_row_index") != metadata.get("row_index")
                or item.get("source_size") != list(size)):
            return dict(result, reason="segment_not_same_physical_row")
        box = _box(item.get("crop_box"), size)
        actual = [r for r in reads if r.get("input_id") == item.get("id")]
        if box is None or len(actual) != 1:
            return dict(result, reason="incomplete_or_invalid_segment_observations")
        row = actual[0]
        score = _confidence(row.get("confidence"))
        if score is None or score < .90 or not _literal(row.get("text")):
            return dict(result, reason="segment_confidence_below_090")
        observations[key] = {"text": str(row["text"]), "confidence": score,
                             "input_id": item["id"], "crop_box": box,
                             "image_url": item.get("image_url", "")}
    expected_keys = {(kind, part, scale) for kind in ("color", "gray")
                     for part, scale in (("left", 1), ("right", 1), ("right", 2))}
    if set(observations) != expected_keys:
        return dict(result, reason="incomplete_segment_representations")
    left_values = [observations[(kind, "left", 1)] for kind in ("color", "gray")]
    right_values = [observations[(kind, "right", scale)] for kind in ("color", "gray") for scale in (1, 2)]
    result["observations"] = left_values + right_values
    left_box, right_box = left_values[0]["crop_box"], right_values[0]["crop_box"]
    if (any(row["crop_box"] != left_box for row in left_values)
            or any(row["crop_box"] != right_box for row in right_values)
            or left_box[2] != right_box[0] or left_box[1] != right_box[1]
            or left_box[3] != right_box[3]):
        return dict(result, reason="segment_geometry_not_one_complete_physical_row")
    lefts = {_literal(row["text"]) for row in left_values}
    rights = {_literal(row["text"]) for row in right_values}
    if len(lefts) != 1 or len(rights) != 1:
        return dict(result, reason="literal_segment_disagreement")
    left, code = next(iter(lefts)), next(iter(rights))
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,}[A-Za-z]{0,3}", left):
        return dict(result, reason="left_segment_not_observed_name_and_optional_letter_prefix")
    if not re.fullmatch(r"[A-Z0-9]{3,12}", code):
        return dict(result, reason="right_segment_not_complete_observed_alphanumeric_token")
    title = left + code
    conflicts = [{"text": row.get("text", ""), "confidence": row.get("confidence"),
                  "reason": "reliable_whole_row_disagrees_with_literal_segments"}
                 for row in whole_row_reads or []
                 if _confidence(row.get("confidence")) is not None
                 and _confidence(row.get("confidence")) >= .75
                 and _literal(row.get("text")) and _literal(row["text"]) != title]
    requires_review = bool(conflicts) or whole_row_reads is None
    return dict(result, accepted=True, title_text=title, left=left, code=code,
                reason="literal_segments_concordant_across_two_views_and_code_scale",
                minimum_confidence=min(row["confidence"] for row in result["observations"]),
                conflicts=conflicts, requires_review=requires_review,
                matching_allowed=not requires_review)
