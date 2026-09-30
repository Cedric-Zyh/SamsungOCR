"""Date-only normalization for the current single-provider OCR flow."""

from __future__ import annotations

import re

from receipt_ocr.domain.ocr import TextObservation


_DATE_ONLY_TRANSLATION = str.maketrans(
    {
        "Ｏ": "0", "０": "0", "１": "1", "２": "2", "３": "3",
        "４": "4", "５": "5", "６": "6", "７": "7", "８": "8",
        "９": "9", "O": "0", "o": "0", "〇": "0",
    }
)


def normalize_date_only_text(text: str) -> str:
    """Keep OCR-owned date characters and normalize numeric separators."""
    compact = re.sub(r"\s+", "", str(text or "")).translate(_DATE_ONLY_TRANSLATION)
    filtered = "".join(char for char in compact if char.isdigit() or char in "年月日-./")
    match = re.search(
        r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)",
        filtered,
    )
    if match:
        year, month, day = match.groups()
        return f"{year}年{int(month)}月{int(day)}日"
    return filtered


def normalize_date_only_rows(rows: list[TextObservation]) -> list[TextObservation]:
    """Return observations containing only date text while retaining geometry."""
    normalized: list[TextObservation] = []
    for row in rows:
        text = normalize_date_only_text(row.text)
        if not text:
            continue
        normalized.append(type(row)(
            text=text,
            confidence=row.confidence,
            x=row.x,
            y=row.y,
            width=row.width,
            height=row.height,
        ))
    return normalized


def sanitize_date_artifacts(artifacts: list[dict]) -> list[dict]:
    """Restrict persisted date OCR text to the same clean-input contract."""
    variant_keys = (
        "ocr_variants",
        "secondary_ocr_variants",
        "date_line_ocr_variants",
        "date_line_display_ocr_variants",
    )
    for artifact in artifacts:
        artifact["ocr_texts"] = [
            value for value in (
                normalize_date_only_text(text)
                for text in artifact.get("ocr_texts") or []
            ) if value
        ]
        if "decision_rows" in artifact:
            rows = []
            for row in artifact.get("decision_rows") or []:
                if not isinstance(row, dict):
                    continue
                value = normalize_date_only_text(row.get("text", ""))
                if value:
                    copied = dict(row)
                    copied["text"] = value
                    rows.append(copied)
            artifact["decision_rows"] = rows
        for key in variant_keys:
            for variant in artifact.get(key) or []:
                variant["ocr_texts"] = [
                    value for value in (
                        normalize_date_only_text(text)
                        for text in variant.get("ocr_texts") or []
                    ) if value
                ]
                if "accepted_texts" in variant:
                    variant["accepted_texts"] = [
                        value for value in (
                            normalize_date_only_text(text)
                            for text in variant.get("accepted_texts") or []
                        ) if value
                    ]
    return artifacts
