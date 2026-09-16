"""Locate receipt footer anchors and exclude printed labels from seal context."""

from __future__ import annotations
from .parser import normalize_text
from .ocr_types import TextObservation


def _find_signature_requirement_row(
    rows: list[TextObservation],
) -> TextObservation | None:
    """Locate the receipt footer even when a stamp hides its first label.

    Paddle occasionally reads ``签章要求`` as ``[b章要求`` when a pale stamp
    crosses the first two characters.  Requiring the exact label then makes a
    complete one-page receipt look like a cover awaiting a continuation page.
    ``章要求`` is still specific to this footer and is accepted only in the
    normal lower-page band.  A heavy customer stamp can also erase that whole
    label while leaving the adjacent fixed note and signing-table labels.  In
    that case require several independent lower-page anchors before inferring
    the footer; one isolated ``日期``/``盖章`` is deliberately insufficient.
    """
    explicit = next(
        (
            row
            for row in rows
            if 0.35 <= row.y <= 0.75
            and (
                any(token in row.text for token in ("签章要求", "签幸要求", "签草要求"))
                or "章要求" in row.text
            )
        ),
        None,
    )
    if explicit is not None:
        return explicit

    evidence: dict[str, TextObservation] = {}
    anchor_tokens = {
        "签收说明": ("签收说明", "整单完整签收", "整单莞整签收"),
        "实收数量": ("实收数量",),
        "拒收数量": ("拒收数量",),
        "收货客户": ("收货客户", "收货客"),
        "仓库接收人": ("仓库接收人", "仓库接收"),
        "货物所有权人": ("货物所有权人",),
        "盖章": ("盖章",),
    }
    for row in rows:
        if not 0.35 <= row.y <= 0.90:
            continue
        text = normalize_text(row.text)
        for name, tokens in anchor_tokens.items():
            if name not in evidence and any(token in text for token in tokens):
                evidence[name] = row
    signing_table = {
        "实收数量",
        "拒收数量",
        "收货客户",
        "仓库接收人",
        "货物所有权人",
        "盖章",
    }
    if (
        "签收说明" not in evidence
        or len(signing_table.intersection(evidence)) < 2
        or len(evidence) < 3
    ):
        return None
    first = min(evidence.values(), key=lambda row: row.y)
    # ``签收说明`` is normally one printed row below ``签章要求``.  Move the
    # inferred anchor slightly upward so the existing tight/wide date crops
    # retain the same geometry as a directly recognized requirement label.
    return TextObservation(
        text="推断签收页脚",
        confidence=min(row.confidence for row in evidence.values()),
        x=first.x,
        y=max(0.35, first.y - max(0.012, first.height * 1.2)),
        width=first.width,
        height=first.height,
    )


def _exclude_printed_footer_rows_from_seal_context(
    rows: list[TextObservation],
) -> list[TextObservation]:
    """Do not let black requirement/note text masquerade as red stamp OCR.

    Full-page OCR can recognize printed values next to the ``签章要求`` and
    ``签收说明`` labels.  When a large stamp region overlaps those lines,
    feeding the black text into seal matching creates a circular perfect
    match.  Color-isolated crop OCR remains available as independent stamp
    evidence; only the contaminated full-page rows on these two form lines
    are removed.
    """
    anchors = [
        row
        for row in rows
        if any(
            label in row.text
            for label in ("签章要求", "签幸要求", "签草要求", "章要求", "签收说明")
        )
    ]
    if not anchors:
        return rows
    output = []
    for row in rows:
        center_y = row.y + row.height / 2
        on_printed_line = any(
            abs(center_y - (anchor.y + anchor.height / 2))
            <= max(0.012, anchor.height * 1.1, row.height * 1.1)
            for anchor in anchors
        )
        if not on_printed_line:
            output.append(row)
    return output
