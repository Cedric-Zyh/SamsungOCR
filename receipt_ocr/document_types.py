from __future__ import annotations

import re

from .ocr_types import TextObservation


DOCUMENT_TYPE_LABELS = {
    "receipt": "三星出库回单",
    "product_continuation": "商品明细续页",
    "warehouse_authorization": "仓库货物接收委托书",
    "unknown": "未知文档",
}

_RECEIPT_FIELDS = (
    "承运商", "运单号", "客户订单号", "客户名称", "要求到货",
    "收货地址", "签章要求", "签收说明",
)
_PRODUCT_HEADERS = (
    "行号", "产品类别", "物料编号", "等级", "出库仓库",
    "数量", "重量", "体积", "ean码",
)
_AUTHORIZATION_FEATURES = (
    "仓库货物接收委托书", "本公司全权委托", "仓库联系人",
    "仓库收货章", "委托公司名称",
)


def _compact(text: str) -> str:
    return re.sub(r"[\s:：,，.。()（）\[\]【】_\-/]", "", text).lower()


def continuation_row_evidence(rows: list[TextObservation]) -> dict:
    """Count geometrically complete product rows on a page without a header."""
    row_numbers = [
        row for row in rows
        if row.x + row.width / 2 < 0.12 and re.fullmatch(r"\d{1,4}", row.text.strip())
    ]
    materials = [
        row for row in rows
        if 0.18 <= row.x + row.width / 2 <= 0.47
        and len(_compact(row.text)) >= 8
        and re.search(r"[A-Za-z]", row.text)
        and re.search(r"\d", row.text)
    ]
    eans = [
        row for row in rows
        if row.x + row.width / 2 >= 0.80
        and re.fullmatch(r"\d{12,14}", re.sub(r"\D", "", row.text))
    ]

    def center_y(item: TextObservation) -> float:
        return item.y + item.height / 2

    complete = []
    for number in row_numbers:
        y = center_y(number)
        material = next((item for item in materials if abs(center_y(item) - y) <= 0.015), None)
        ean = next((item for item in eans if abs(center_y(item) - y) <= 0.015), None)
        if material and ean:
            complete.append(number)
    return {
        "complete_rows": len(complete),
        "row_numbers": len(row_numbers),
        "materials": len(materials),
        "eans": len(eans),
        "first_y": min((row.y for row in complete), default=None),
        "last_y": max((row.y for row in complete), default=None),
    }


def classify_document(rows: list[TextObservation]) -> dict:
    """Classify a page before applying the fixed receipt verification template.

    The source folder contains receipt cover pages, product continuation pages,
    and warehouse authorization letters.  Only a confidently identified receipt
    cover may enter the fixed date/stamp pipeline.
    """
    text = _compact("\n".join(row.text for row in rows if row.text))
    receipt_hits = [token for token in _RECEIPT_FIELDS if _compact(token) in text]
    product_hits = [token for token in _PRODUCT_HEADERS if _compact(token) in text]
    authorization_hits = [
        token for token in _AUTHORIZATION_FEATURES if _compact(token) in text
    ]
    has_receipt_title = "出库单" in text
    has_authorization_title = "仓库货物接收委托书" in text
    continuation_evidence = continuation_row_evidence(rows)

    if has_authorization_title or len(authorization_hits) >= 2:
        confidence = 0.99 if has_authorization_title else min(0.94, 0.70 + 0.08 * len(authorization_hits))
        kind = "warehouse_authorization"
        reasons = ["识别到委托书标题或专有字段：" + "、".join(authorization_hits)]
    elif has_receipt_title and len(receipt_hits) >= 2 or len(receipt_hits) >= 4:
        confidence = min(0.99, 0.70 + 0.04 * len(receipt_hits) + (0.10 if has_receipt_title else 0))
        kind = "receipt"
        reasons = ["识别到回单标题/业务字段：" + "、".join(receipt_hits)]
    elif (
        len(product_hits) >= 4 or continuation_evidence["complete_rows"] >= 3
    ) and len(receipt_hits) < 3:
        confidence = min(
            0.98,
            0.68 + 0.04 * len(product_hits)
            + 0.025 * min(8, continuation_evidence["complete_rows"]),
        )
        kind = "product_continuation"
        if product_hits:
            reasons = ["识别到商品表头但缺少回单首页字段：" + "、".join(product_hits)]
        else:
            reasons = [
                f"识别到 {continuation_evidence['complete_rows']} 行无表头商品明细，"
                "且缺少回单首页字段"
            ]
    else:
        evidence = receipt_hits + product_hits + authorization_hits
        confidence = min(0.65, 0.20 + 0.04 * len(evidence))
        kind = "unknown"
        reasons = [
            "未获得足以安全套用回单模板的版式证据"
            + ("；局部证据：" + "、".join(evidence) if evidence else "")
        ]

    return {
        "type": kind,
        "label": DOCUMENT_TYPE_LABELS[kind],
        "confidence": round(confidence, 3),
        "reliable": confidence >= 0.72,
        "reasons": reasons,
        "evidence": {
            "receipt_fields": receipt_hits,
            "product_headers": product_hits,
            "authorization_fields": authorization_hits,
            "receipt_title": has_receipt_title,
            "authorization_title": has_authorization_title,
            "continuation_rows": continuation_evidence,
        },
    }
