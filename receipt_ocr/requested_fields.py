"""Read explicit page evidence; never infer a person's name or missing quantity."""

import re

from .parsing_constants import LOW_CONFIDENCE_THRESHOLD, PRODUCT_COLUMN_RANGES


_CONTACT_LABELS = ("仓库联系人", "联系人")
_CONTACT_NOISE = {
    "座机", "电话", "手机", "传真", "发货单位", "收货单位", "仓库联系人",
    "联系人", "收货人", "仓库", "地址", "广东", "电话号",
}


def contact_candidates_from_text(text):
    """Extract conservative name candidates from one OCR line.

    Contact rows often contain a label, a landline/mobile number, and one or
    more names in the same line.  The old rule stopped at the first digit and
    therefore returned the label ``座机`` as a person's name.  Prefer Chinese
    runs immediately before a phone-like number; fall back to an explicit
    contact label when no number is present.
    """
    text = str(text or "").strip()
    if not text:
        return []
    has_contact_label = any(label in text for label in _CONTACT_LABELS)
    phone_pattern = r"(?:1\d{10}|0\d{2,3}[- ]?\d{7,8}|\d{5,})"
    numbered = re.findall(rf"([\u4e00-\u9fff]{{1,4}})\s*(?={phone_pattern})", text)
    candidates = numbered
    if not candidates and has_contact_label:
        remainder = text
        for label in _CONTACT_LABELS:
            remainder = remainder.replace(label, " ")
        # An explicit label gives permission to accept an unnumbered name,
        # but still rejects labels such as “座机” and “收货地址”.
        candidates = re.findall(r"[\u4e00-\u9fff]{1,4}", remainder)
    output = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate in _CONTACT_NOISE or any(noise in candidate for noise in ("地址", "单位", "客户", "收货")):
            continue
        if candidate not in output:
            output.append(candidate)
    return output


def _near_line(a, b):
    return abs((a.y + a.height / 2) - (b.y + b.height / 2)) <= max(a.height, b.height) * .65


def _printed_contact_evidence(rows):
    """Use the same selected OCR row for extraction and confidence scoring."""
    shipping = next((r for r in rows if "发货单位" in r.text), None)
    if shipping:
        candidates = [r for r in rows if 0 < shipping.y - r.y < .055 and r.x < .55]
        for row in sorted(candidates, key=lambda r: shipping.y - r.y):
            names = contact_candidates_from_text(row.text)
            if names:
                return row, names
    return None, []


def contact_confidence_metadata(value, rows):
    """Score each name against its extraction evidence, not the joined string.

    OCR exposes row scores, not character probabilities. Reuse that score
    without a phone-number length penalty or a heuristic confidence bonus.
    The weakest name controls the field warning; a surname alone remains
    subject to review even if its character was read confidently.
    """
    evidence_row, extracted_names = _printed_contact_evidence(rows)
    names = list(dict.fromkeys(
        name.strip() for name in re.split(r"[、,，/／;；|]+", str(value or ""))
        if name.strip()
    ))
    candidates = []
    for name in names:
        supported = evidence_row is not None and name in extracted_names
        ocr_confidence = max(0.0, min(1.0, float(evidence_row.confidence))) if supported else None
        incomplete = len(name) < 2
        confidence = min(ocr_confidence, 0.68) if supported and incomplete else (ocr_confidence or 0.0)
        candidates.append({
            "name": name,
            "confidence": round(confidence, 3),
            "ocr_confidence": ocr_confidence,
            "evidence_text": evidence_row.text if supported else "",
            "reason": "姓名不完整" if supported and incomplete else "OCR 来源行" if supported else "未找到姓名来源证据",
        })
    confidence = min((item["confidence"] for item in candidates), default=0.0)
    return {
        "original": value,
        "value": value,
        "confidence": confidence,
        "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
        "source": "OCR（姓名来源行，取最低分）",
        "candidates": candidates,
    }


def printed_extras(rows):
    _, names = _printed_contact_evidence(rows)
    fields = {"仓库联系人": "、".join(names), "合计数量": ""}
    total = next((r for r in rows if re.match(r"^合计\s*[:：]?", r.text.strip())), None)
    if total:
        left, right = PRODUCT_COLUMN_RANGES["数量"]
        candidates = [r for r in rows if _near_line(r, total) and left <= r.x + r.width / 2 < right and re.fullmatch(r"\d+", r.text.strip())]
        if len(candidates) == 1:
            fields["合计数量"] = candidates[0].text.strip()
    return fields


def handwritten_candidates(rows):
    """Conservative candidates, not a handwriting-style classifier.

    Restrict reading to explicit labels and keep every nonempty value subject
    to human confirmation. Do not scan remarks for unrelated names/numbers.
    """
    fields = {"仓库接收人": ""}
    labels = {"仓库接收人": ("仓库接收人", "收货人签名", "签收人", "仓管签名")}
    for name, aliases in labels.items():
        anchor = next((r for r in rows if any(re.match(r"^\s*" + label, r.text) for label in aliases)), None)
        if anchor is None:
            continue
        label = next(label for label in aliases if label in anchor.text)
        inline = anchor.text.split(label, 1)[1].strip(" ：:")
        # A neighboring label terminates the field even when OCR merged boxes.
        inline = re.split(r"实收数量|拒收数量|日期|仓库接收人", inline)[0]
        nearby = [r for r in rows if r is not anchor and _near_line(r, anchor)
                  and 0 <= r.x - (anchor.x + anchor.width) < .10]
        candidates = [inline] + [r.text for r in sorted(nearby, key=lambda r: r.x)]
        for text in candidates:
            text = text.strip(" ：:")
            text = re.split(r"\d|日期", text)[0].strip()
            value = text if re.fullmatch(r"[\u4e00-\u9fff]{1,4}|[A-Za-z]{1,30}(?: [A-Za-z]{1,30})?", text) else ""
            if any(x in value for x in ("签名", "接收", "收货", "盖章", "未签", "年月", "备注", "数量")):
                value = ""
            if value:
                fields[name] = value
                break
    return fields
