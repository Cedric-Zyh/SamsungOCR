"""Read explicit page evidence; never infer a person's name or missing quantity."""

import re

from .parser import PRODUCT_COLUMN_RANGES


def _near_line(a, b):
    return abs((a.y + a.height / 2) - (b.y + b.height / 2)) <= max(a.height, b.height) * .65


def printed_extras(rows):
    fields = {"仓库联系人": "", "合计数量": ""}
    shipping = next((r for r in rows if "发货单位" in r.text), None)
    if shipping:
        candidates = [r for r in rows if 0 < shipping.y - r.y < .055 and r.x < .55]
        for row in sorted(candidates, key=lambda r: shipping.y - r.y):
            # Names must be actual text on the line above the shipping unit.
            # Reject addresses and labels instead of turning them into names.
            text = re.sub(r"^(?:仓库联系人|联系人)\s*[:：]?", "", row.text.strip())
            text = re.split(r"\d|电话|手机|[：:]", text)[0].strip()
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text) and not any(x in text for x in ("地址", "客户", "收货", "仓库", "单位")):
                fields["仓库联系人"] = text
                break
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
