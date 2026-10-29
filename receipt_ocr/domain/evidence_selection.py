"""Shared evidence quality rules for choosing recognized field candidates."""

from math import isfinite


_INACTIVE = {
    "failed", "error", "skipped", "pending", "waiting", "running", "submitted",
    "queued", "uploading", "submitting", "polling", "paused",
    "not_run", "not_executed", "unexecuted", "cancelled", "canceled",
    "未执行", "未运行", "识别失败", "运行失败", "待识别", "识别中", "测试中",
}



def inactive(evidence):
    if not isinstance(evidence, dict):
        return False
    return bool(
        evidence.get("error")
        or evidence.get("simulated")
        or evidence.get("pending")
        or evidence.get("mode") in {"mock", "simulated", "demo"}
        or any(str(evidence.get(key, "")).lower() in _INACTIVE
               for key in ("status", "execution_status"))
    )


def confidence(metadata):
    # provider_ratio is provider-specific evidence, not a calibrated probability.
    value = metadata.get("confidence")
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) and 0 <= value <= 1 else None


def _quality(candidate):
    metadata, confidence = candidate["metadata"], candidate["confidence"]
    if metadata.get("low_confidence") is True or (confidence is not None and confidence < .65):
        return 0
    if confidence is not None and confidence >= .85:
        return 3
    if metadata.get("low_confidence") is False or confidence is not None:
        return 2
    return 1


def _rank(candidate):
    confidence = candidate["confidence"]
    return _quality(candidate), confidence if confidence is not None else -1



def choose_candidate(candidates, *, label="字段"):
    if not candidates:
        return None, False, f"未识别到{label}"
    groups = {}
    for candidate in candidates:
        groups.setdefault(candidate["normalized"], []).append(candidate)
    selected = max(candidates, key=_rank)
    if len(groups) == 1:
        reason = "多个识别结果一致" if len(candidates) > 1 else f"采用识别到的{label}"
        return selected, False, reason

    # Agreement between independent, usable readers is stronger evidence than
    # repeated attempts from a single reader. Low-confidence guesses do not vote.
    support = {value: len({c["method"] for c in group if _quality(c) > 0 and c["method"]})
               for value, group in groups.items()}
    strongest = max(support, key=support.get)
    if support[strongest] >= 2 and all(support[strongest] > count
                                     for value, count in support.items() if value != strongest):
        return max(groups[strongest], key=_rank), False, "优先采用多个识别方式一致的结果"

    others = [c for c in candidates if c["normalized"] != selected["normalized"]]
    if (_quality(selected) >= 2
            and all(c["metadata"].get("low_confidence") is True for c in others)):
        return selected, False, "优先采用可信度更高的识别结果"
    confidence = selected["confidence"]
    if (confidence is not None and confidence >= .85 and _quality(selected) > 0
            and all(c["confidence"] is not None and confidence - c["confidence"] >= .15
                    for c in others)):
        return selected, False, "优先采用可信度更高的识别结果"
    return selected, True, f"{label}识别结果不同，已显示证据较强的候选，仍需核对原图"

