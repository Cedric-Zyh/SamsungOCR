"""Prefer a complete observed type row over a truncated rereading."""
import math

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import normalize_seal_text_strict


def prefer_complete_type_read(observations, input_image, artifact):
    alternative = input_image.get("verified_type_read")
    if not alternative:
        return observations
    text = str(alternative.get("text") or "")
    confidence = float(alternative.get("confidence") or 0)
    if not text or not math.isfinite(confidence) or confidence < .8:
        return observations
    current = "".join(row.text for row in observations if row.text)

    def key(value):
        # Only for comparing completeness; the original OCR remains unchanged.
        return normalize_seal_text_strict(value).replace("貨", "货")

    old, new = key(current), key(text)
    # A longer conflicting row or unrelated ring text is not more complete.
    if len(new) <= len(old) or old not in new or not new.endswith("章"):
        return observations
    artifact["type_row_selection"] = {
        "reason": "prefer_complete_observed_row",
        "candidates": [
            {"text": current, "image_url": input_image["image_url"],
             "confidences": [row.confidence for row in observations]},
            dict(alternative),
        ],
        "selected_text": text, "selected_image_url": alternative["image_url"],
    }
    input_image["selected_read_image_url"] = alternative["image_url"]
    artifact["round_type_band_url"] = alternative["image_url"]
    return [TextObservation(text, confidence, 0, 0, 1, 1)]
