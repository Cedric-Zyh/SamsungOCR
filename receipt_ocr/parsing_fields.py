"""Printed field extraction, ordered contextual repairs and field confidence."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from .field_identity import (
    _clean_organization_boundaries,
    _repair_destination_business_codes,
    _repair_receipt_address,
    _repair_shipping_unit,
    _repair_verified_customer_identity,
)
from .field_requirements import (
    _clean_requirement_footer_overlap,
    _repair_customer_requirement_master,
    _repair_repeated_company_requirement,
    _repair_requirement_company_prefix,
    _repair_requirement_stamp_type,
    _repair_service_center_requirement,
)
from .ocr_types import TextObservation
from .parsing_constants import (
    FIELD_LABELS,
    FIXED_FIELD_PHRASES,
    FIXED_PHRASE_MIN_SIMILARITY,
    FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD,
    LOW_CONFIDENCE_THRESHOLD,
)
from .parsing_dates import parse_date
from .parsing_text import normalize_text


def _strip_label(text: str, label: str) -> str:
    pattern = rf"^\s*{re.escape(label)}\s*[:：]?\s*"
    return re.sub(pattern, "", text, count=1).strip(" _：:")


def _same_line_values(rows: list[TextObservation], label_row: TextObservation) -> list[TextObservation]:
    tolerance = max(0.010, label_row.height * 0.85)
    return sorted(
        [
            row
            for row in rows
            if row is not label_row
            and row.x >= label_row.x + label_row.width * 0.65
            and abs((row.y + row.height / 2) - (label_row.y + label_row.height / 2)) <= tolerance
        ],
        key=lambda row: row.x,
    )


def extract_field(rows: list[TextObservation], labels: Iterable[str]) -> str:
    normalized_labels = [normalize_text(label) for label in labels]
    for row in rows:
        normalized = normalize_text(row.text)
        for label, normalized_label in zip(labels, normalized_labels):
            if not normalized.startswith(normalized_label):
                continue
            direct = _strip_label(row.text, label)
            if direct and re.search(r"[0-9A-Za-z\u4e00-\u9fff]", direct) and normalize_text(direct) != normalized_label:
                return direct
            candidates = _same_line_values(rows, row)
            for candidate in candidates:
                value = candidate.text.strip()
                # A detached colon/table mark can sit between a label and its
                # real value. It is geometry-compatible but not field data.
                if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", value):
                    return value
    return ""


def extract_joined_field(rows: list[TextObservation], label: str) -> str:
    """Join adjacent OCR boxes that belong to one long single-line field."""
    label_row = _find_label_row(rows, label)
    if not label_row:
        return ""
    parts: list[str] = []
    direct = _strip_label(label_row.text, label)
    if direct and normalize_text(direct) != normalize_text(label):
        parts.append(direct)
    for candidate in _same_line_values(rows, label_row):
        # The requirement occupies the left/middle portion of its row.  Stop
        # before the receiving-quantity boxes on the far right.
        if candidate.x >= 0.72:
            continue
        if any(token in normalize_text(candidate.text) for token in ("实收数量", "拒收数量")):
            break
        if any(normalize_text(candidate.text).startswith(normalize_text(other)) for other in FIELD_LABELS):
            continue
        parts.append(candidate.text.strip())
    return "".join(part for part in parts if part)


def parse_fields(rows: list[TextObservation]) -> dict[str, str]:
    fields = {name: extract_field(rows, labels) for name, labels in FIELD_LABELS.items()}
    # Long customer/warehouse values can be merged with the printed business
    # code on the right side of the same line.  The label is an explicit field
    # boundary; strip only that suffix (including common OCR l/1 confusion).
    code_suffix = re.compile(
        r"\s*(?:S[o0][l1]dToC[o0a]de|ShipToC[o0a]de)\s*[:：]?\s*\d.*$",
        re.IGNORECASE,
    )
    embedded_codes = (
        ("客户名称", "SoldToCode", re.compile(r"S[o0][l1]dToC[o0a]de\s*[:：]?\s*(\d+)", re.I)),
        ("客户仓库", "ShipToCode", re.compile(r"ShipToC[o0a]de\s*[:：]?\s*(\d+)", re.I)),
    )
    for name, code_name, code_pattern in embedded_codes:
        if fields.get(name):
            match = code_pattern.search(fields[name])
            if match:
                fields[code_name] = match.group(1)
            fields[name] = code_suffix.sub("", fields[name]).strip()
    # Paddle may split one printed date into ``要求到货：2`` and a separate
    # ``2025-01-23`` box.  Do not keep the stray digit when a valid same-line
    # date is present as independent OCR evidence.
    if not parse_date(fields.get("要求到货", "")):
        date_label = _find_label_row(rows, "要求到货")
        if date_label:
            for candidate in _same_line_values(rows, date_label):
                parsed = parse_date(candidate.text)
                if parsed:
                    fields["要求到货"] = parsed.isoformat()
                    break
    joined_requirement = extract_joined_field(rows, "签章要求")
    if joined_requirement:
        fields["签章要求"] = joined_requirement
    # The current Samsung receipt template uses this confirmed fixed note.
    # Fill it only when the dedicated label is present and OCR lost the whole
    # value; documents without the label are never completed.
    if not fields.get("签收说明") and _find_label_row(rows, "签收说明"):
        fields["签收说明"] = FIXED_FIELD_PHRASES["签收说明"][0]
    for row in rows:
        text = row.text.strip()
        if text.lower().startswith("soldtocode"):
            fields["SoldToCode"] = _after_colon(text)
        elif text.lower().startswith("shiptocode"):
            fields["ShipToCode"] = _after_colon(text)

    if not fields.get("客户地址"):
        customer_name_row = _find_label_row(rows, "客户名称")
        warehouse_row = _find_label_row(rows, "客户仓库")
        if customer_name_row and warehouse_row:
            between = [
                row
                for row in rows
                if customer_name_row.y + 0.008 < row.y < warehouse_row.y - 0.002
                and 0.08 < row.x < 0.72
                and "code" not in row.text.lower()
            ]
            if between:
                fields["客户地址"] = max(between, key=lambda row: row.width).text

    # Contact line sits between receipt address and shipping-unit rows in this template.
    receipt_address_row = _find_label_row(rows, "收货地址")
    shipping_unit_row = _find_label_row(rows, "发货单位")
    contact_y1 = receipt_address_row.y + 0.018 if receipt_address_row else 0.315
    contact_y2 = shipping_unit_row.y - 0.004 if shipping_unit_row else 0.355
    contact_rows = [row.text for row in rows if contact_y1 <= row.y <= contact_y2 and row.x < 0.55]
    if contact_rows:
        fields["收货联系人信息"] = " ".join(contact_rows)

    signature_row = _find_label_row(rows, "签章要求")
    detail_y2 = signature_row.y - 0.006 if signature_row else 0.465
    detail_rows = [
        row.text
        for row in rows
        if 0.395 <= row.y <= detail_y2
        and row.text not in {"行号", "产品类别", "物料编号", "等级", "出库仓库", "数量", "重量", "体积", "EAN码", "合计："}
    ]
    fields["商品明细原文"] = " | ".join(detail_rows)
    return {key: value for key, value in fields.items() if value}


def standardize_fixed_phrases(fields: dict[str, str]) -> dict[str, dict]:
    """Correct high-similarity fixed template phrases while retaining raw OCR evidence."""
    corrections: dict[str, dict] = {}
    for name, phrases in FIXED_FIELD_PHRASES.items():
        original = str(fields.get(name, "")).strip()
        normalized_original = normalize_text(original)
        if not normalized_original:
            continue
        best_phrase = ""
        best_similarity = 0.0
        for phrase in phrases:
            similarity = SequenceMatcher(
                None, normalized_original, normalize_text(phrase)
            ).ratio()
            if similarity > best_similarity:
                best_phrase, best_similarity = phrase, similarity
        threshold = FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD.get(
            name, FIXED_PHRASE_MIN_SIMILARITY
        )
        # The user confirmed this line is fixed on the current Samsung
        # template.  A clipped OCR value beginning with the distinctive
        # opening ``如未/如末`` is sufficient template evidence; unrelated
        # note text is still never replaced.
        fixed_note_prefix = bool(
            name == "签收说明"
            and re.match(r"^如[未末]", normalized_original)
        )
        # On a reviewed sample the left half of the fixed note was replaced by
        # unrelated OCR glyphs while the long, highly distinctive right-hand
        # template suffix remained intact.  This suffix is stronger evidence
        # than a generic fuzzy score and is deliberately limited to this one
        # confirmed field/template pair.
        fixed_note_suffix = bool(
            name == "签收说明"
            and normalized_original.endswith("数量视为整单完整签收")
        )
        if best_similarity < threshold and not (fixed_note_prefix or fixed_note_suffix):
            continue
        fields[name] = best_phrase
        # 模板规则提高结论置信度，但仍保留与相似度相关的上限。
        confidence = round(
            0.93 if (fixed_note_prefix or fixed_note_suffix) and best_similarity < threshold
            else min(0.99, 0.80 + 0.19 * best_similarity),
            3,
        )
        corrections[name] = {
            "original": original,
            "value": best_phrase,
            "similarity": round(best_similarity, 3),
            "confidence": confidence,
            "source": (
                "固定签收说明前缀/特征后缀 + 模板标准短语校正"
                if (fixed_note_prefix or fixed_note_suffix) and best_similarity < threshold
                else "模板标准短语校正"
            ),
        }
    return corrections


def repair_contextual_fields(fields: dict[str, str]) -> dict[str, dict]:
    """Repair OCR truncation/typos only when another field is strong evidence.

    The original value is returned with every correction so the UI/database can
    keep the immutable machine reading.  The stamp requirement may borrow the
    already-recognized customer company prefix only when the two prefixes are
    demonstrably the same organization.  Printed organization fields are never
    completed merely because they appear to end in ``有限``.
    """
    corrections: dict[str, dict] = {}

    # Preserve this order: earlier identity repairs establish evidence used by
    # later requirement rules; cleanup can also expose a suffix for rechecking.
    # Helpers mutate only the supplied fields/corrections and return the
    # normalized organization values needed by the next rule group.
    _clean_organization_boundaries(fields, corrections)
    _repair_shipping_unit(fields, corrections)
    _repair_receipt_address(fields, corrections)
    customer, warehouse, requirement = _repair_destination_business_codes(
        fields, corrections
    )
    customer, warehouse = _repair_verified_customer_identity(
        fields, corrections, customer, warehouse, requirement
    )
    customer, requirement = _repair_repeated_company_requirement(
        fields, corrections, customer, warehouse
    )
    requirement = _repair_service_center_requirement(fields, corrections, requirement)
    requirement = _clean_requirement_footer_overlap(
        fields, corrections, customer, warehouse, requirement
    )
    _repair_requirement_company_prefix(
        fields, corrections, customer, warehouse, requirement
    )
    _repair_requirement_stamp_type(fields, corrections, customer, warehouse)
    _repair_customer_requirement_master(fields, corrections, customer, warehouse)
    return corrections


def enrich_fields(fields: dict[str, str], qr_text: str = "") -> dict[str, str]:
    output = dict(fields)
    tracking = output.get("运单号", "")
    match = re.search(r"W(20\d{2})(\d{2})(\d{2})", tracking)
    # The tracking number is a fallback, not the source of truth. Some real
    # receipts were created several days after the tracking-number date.
    if match and parse_date(output.get("制单日期", "")) is None:
        output["制单日期"] = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    if qr_text:
        query = parse_qs(urlparse(qr_text).query)
        order_id = (query.get("ORDER_ID") or [""])[0]
        booking = (query.get("CUSTOMER_BK_NO") or [""])[0]
        if order_id:
            output["客户订单号"] = order_id
        if booking:
            output["二维码业务编号"] = booking
    return output


def estimate_field_confidences(
    fields: dict[str, str],
    rows: list[TextObservation],
    qr_text: str = "",
) -> dict[str, dict]:
    """Attach explainable per-field confidence without changing extracted values."""
    metadata: dict[str, dict] = {}
    qr_fields = {"客户订单号", "二维码业务编号"} if qr_text else set()
    for name, value in fields.items():
        source = "OCR"
        if name in qr_fields:
            confidence, source = 0.99, "二维码"
        elif name == "制单日期" and re.match(r"^20\d{2}-\d{2}-\d{2}$", value):
            confidence, source = 0.96, "运单号推导"
        else:
            normalized_value = normalize_text(value)
            scored: list[float] = []
            for row in rows:
                normalized_row = normalize_text(row.text)
                if not normalized_row or not normalized_value:
                    continue
                if normalized_value in normalized_row or normalized_row in normalized_value:
                    overlap = min(len(normalized_value), len(normalized_row)) / max(len(normalized_value), len(normalized_row))
                    scored.append(float(row.confidence) * (0.65 + 0.35 * overlap))
                else:
                    ratio = SequenceMatcher(None, normalized_value, normalized_row).ratio()
                    if ratio >= 0.72:
                        scored.append(float(row.confidence) * ratio)
            confidence = max(scored, default=0.42)
            if name in {"商品明细原文", "收货联系人信息"} and scored:
                confidence = min(confidence, sum(scored) / len(scored) + 0.08)
        if name == "签章要求" and len(normalize_text(value)) < 6:
            # A stamp requirement is normally a company/stamp name. Tiny OCR
            # fragments such as “贵小” may have high glyph confidence but are
            # not semantically complete enough for automatic seal matching.
            confidence = min(confidence, 0.45)
            source = f"{source}（内容过短）"
        confidence = round(max(0.0, min(1.0, confidence)), 3)
        metadata[name] = {
            "original": value,
            "value": value,
            "confidence": confidence,
            "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
            "source": source,
        }
    return metadata


def _find_label_row(rows: list[TextObservation], label: str) -> TextObservation | None:
    wanted = normalize_text(label)
    return next((row for row in rows if normalize_text(row.text).startswith(wanted)), None)


def _after_colon(text: str) -> str:
    parts = re.split(r"[:：]", text, maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else text.strip()
