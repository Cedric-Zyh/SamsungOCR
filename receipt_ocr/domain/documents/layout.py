"""Locate receipt footer anchors and exclude printed labels from seal context."""

from __future__ import annotations
import math
import re
from ..parsing import normalize_text
from ..ocr import TextObservation


def signature_footer_evidence(rows: list[TextObservation], recovered_row=None) -> dict:
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
        return {"present": True, "method": "printed_requirement", "anchor": explicit,
                "labels": ["签章要求"]}

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
        if (not 0.35 <= row.y <= 0.90 or not math.isfinite(row.confidence)
                or row.confidence < .80):
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
    # The fixed note can be hidden together with the requirement label.
    # Independent quantity labels above an actual signing-table label form
    # an alternative, spatially coherent footer; dates alone never do.
    quantities = [evidence[k] for k in ("实收数量", "拒收数量") if k in evidence]
    signing = [evidence[k] for k in ("收货客户", "仓库接收人", "盖章") if k in evidence]
    cluster = []
    for quantity in quantities:
        nearby_quantities = [r for r in quantities
                             if abs(r.y-quantity.y) <= max(.025, quantity.height*1.5)
                             and abs(r.x-quantity.x) <= .4]
        nearby_signing = [r for r in signing if quantity.y+.008 <= r.y <= quantity.y+.14
                          and .40 <= r.x <= .98]
        if len(nearby_quantities) == 2 and nearby_signing:
            cluster = nearby_quantities + nearby_signing
            break
    method = "quantity_and_signing_table"
    if not cluster:
        legacy = ("签收说明" in evidence and len(signing_table.intersection(evidence)) >= 2
                  and max(r.y for r in evidence.values())-min(r.y for r in evidence.values()) <= .18)
        if legacy:
            cluster, method = list(evidence.values()), "note_and_signing_table"
    if recovered_row is not None and not cluster:
        raw = re.sub(r"\s+", "", recovered_row.text)
        valid = (math.isfinite(recovered_row.confidence) and recovered_row.confidence >= .90
                 and .35 <= recovered_row.y <= .75
                 and re.match(r"签[章幸草]要求[:：]?.{6,}$", raw))
        nearby_quantities = [r for r in quantities if 0 <= r.y-recovered_row.y <= .07]
        nearby_signing = [r for r in signing if .008 <= r.y-recovered_row.y <= .15]
        if valid and nearby_quantities and nearby_signing:
            return {"present": True, "method": "recovered_requirement_and_table",
                    "anchor": recovered_row, "labels": ["签章要求", *[
                        k for k, r in evidence.items() if r in nearby_quantities+nearby_signing]]}
    if not cluster or len({id(r) for r in cluster}) < 3:
        return {"present": False, "method": "insufficient_footer_evidence", "anchor": None,
                "labels": list(evidence)}
    first = min(cluster, key=lambda row: row.y)
    # ``签收说明`` is normally one printed row below ``签章要求``.  Move the
    # inferred anchor slightly upward so the existing tight/wide date crops
    # retain the same geometry as a directly recognized requirement label.
    anchor = TextObservation(
        text="推断签收页脚",
        confidence=min(row.confidence for row in cluster),
        x=first.x,
        y=max(0.35, first.y - max(0.012, first.height * 1.2)),
        width=first.width,
        height=first.height,
    )
    return {"present": True, "method": method, "anchor": anchor,
            "labels": [k for k, r in evidence.items() if r in cluster]}


def _find_signature_requirement_row(rows: list[TextObservation]) -> TextObservation | None:
    return signature_footer_evidence(rows)["anchor"]


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
