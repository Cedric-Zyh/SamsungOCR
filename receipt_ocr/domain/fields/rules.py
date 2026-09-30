"""Pure rules for printed receipt fields."""

from __future__ import annotations
from difflib import SequenceMatcher
from ..parsing import FIXED_FIELD_PHRASES, normalize_text
from ..ocr import TextObservation


def _is_neighboring_label_misread_as_receipt_note(value: str) -> bool:
    """Reject a nearby form label accidentally selected as the note value."""
    normalized = normalize_text(str(value))
    return normalized in {
        "实收数量",
        "拒收数量",
        "实收数量台",
        "拒收数量台",
        "收货客户",
        "仓库接收人",
        "盖章",
        "备注",
    }


def _recover_confirmed_template_note(rows: list[TextObservation], current: str) -> str:
    """Recover the user-confirmed fixed note when its printed label exists.

    The current Samsung receipt template always prints this exact sentence.
    OCR near the footer can return an unrelated short fragment (for example
    ``物流发``), so the label is the template anchor and ``current`` is kept
    only as immutable machine evidence by the caller.
    """
    del current
    if any(
        normalize_text(row.text).startswith(normalize_text("签收说明"))
        or any(
            token in normalize_text(row.text)
            for token in ("签章要求", "签幸要求", "签草要求")
        )
        for row in rows
    ):
        return FIXED_FIELD_PHRASES["签收说明"][0]
    return ""




def _prefer_detail_requirement(primary: str, detail: str) -> bool:
    """Select a fuller secondary requirement without accepting unrelated text."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(detail_key) < 6:
        return False
    if len(primary_key) < 6:
        return True
    # ``维修专章`` is itself a complete printed stamp type on reviewed
    # Samsung receipts. A detail-page pass may hallucinate the extra ``用``
    # and look one glyph "fuller" than Paddle's exact reading. Keep the
    # primary form value in this one unambiguous insertion case; seal matching
    # handles ``维修专章``/``维修专用章`` as equivalent separately.
    if (
        primary_key.endswith("维修专章")
        and detail_key == primary_key.removesuffix("维修专章") + "维修专用章"
    ):
        return False
    similarity = SequenceMatcher(None, primary_key, detail_key).ratio()
    return similarity >= 0.72 and len(detail_key) >= len(primary_key) + 1


def _prefer_detail_field(primary: str, detail: str) -> bool:
    """Accept a fuller secondary business field only when both readings agree."""
    from difflib import SequenceMatcher

    primary_key = "".join(str(primary).split())
    detail_key = "".join(str(detail).split())
    if len(primary_key) < 6 or len(detail_key) <= len(primary_key):
        return False
    if len(detail_key) > len(primary_key) + 6:
        return False
    return SequenceMatcher(None, primary_key, detail_key).ratio() >= 0.82
