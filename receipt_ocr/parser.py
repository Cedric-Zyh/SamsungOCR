from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from .document_types import continuation_row_evidence
from .ocr_types import TextObservation

LOW_CONFIDENCE_THRESHOLD = 0.72

PRODUCT_COLUMNS = (
    "行号", "产品类别", "物料编号", "等级", "出库仓库", "数量", "重量", "体积", "EAN码",
)

# 固定版式的列边界。使用 OCR 文字框中心点归列，比依赖 OCR 自动插入空格稳定得多。
PRODUCT_COLUMN_RANGES = {
    "行号": (0.00, 0.115),
    "产品类别": (0.115, 0.22),
    "物料编号": (0.22, 0.43),
    "等级": (0.43, 0.49),
    "出库仓库": (0.49, 0.59),
    "数量": (0.59, 0.675),
    "重量": (0.675, 0.755),
    "体积": (0.755, 0.82),
    "EAN码": (0.82, 1.00),
}

PRODUCT_REQUIRED_COLUMNS = {
    "行号", "产品类别", "物料编号", "等级", "出库仓库", "数量", "重量", "体积", "EAN码",
}

# EAN is a check-digit-protected product identifier.  These recurring items
# were confirmed in multiple reviewed receipts; use the catalog only when the
# OCR material is already highly similar, preserving unknown/new products.
VERIFIED_MATERIAL_BY_EAN = {
    "8806094705492": "SM-S9180ZKHCHC悠远黑512G",
    "8806097031062": "F-RS938CBEGCN护盾减震型保护壳",
    "8806095906553": "F-VS938PBEGCN环保生态皮保护壳",
    "8806095307947": "SM-S9280ZKHCHC钛黑512G",
    "8806095787787": "SM-W9025ZDGCHC陶瓷黑1TB",
    "8806095635699": "F-VF741PYEGCN环保生态皮保护壳",
    "8806095299341": "SM-S9260ZKGCHC水墨黑512G",
    "8806095299389": "SM-S9260ZKDCHC水墨黑256G",
    # Independently printed on reviewed receipts 7303351778/7303624019.
    "8806097433736": "SM-F9660ZKGCHC秘影黑512G",
    # Independently printed on reviewed receipt 7320775545. OCR may drop the
    # final colour glyph because ``黑`` touches the following 512G text.
    "8806097727347": "SM-W9026AKDCHC玄曜黑512G",
}
VERIFIED_EAN_BY_MATERIAL = {
    material: ean for ean, material in VERIFIED_MATERIAL_BY_EAN.items()
}


FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "承运商": ("承运商",),
    "运单号": ("运单号",),
    "制单日期": ("制单日期",),
    "供应商": ("供应商", "共应商"),
    "客户名称": ("客户名称",),
    "客户地址": ("客户地址",),
    # PaddleOCR occasionally confuses the printed ``仓`` with ``片`` on the
    # standard template.  Keep this as an explicit observed label alias; it is
    # used only to locate the row and never changes the extracted value.
    "客户仓库": ("客户仓库", "客片仓库", "客仓库"),
    "客户订单号": ("客户订单号",),
    "销售订单号": ("销售订单号", "消售订单号"),
    "要求到货": ("要求到货",),
    # Reviewed PP-OCRv5 sample 7304159004 reads the fixed printed label as
    # ``收le址`` while recognizing the full address value at 99%.  This alias
    # is used only as a geometric label anchor; it never supplies field data.
    "收货地址": ("收货地址", "收le址"),
    "发货单位": ("发货单位",),
    "发货地址": ("发货地址",),
    "签章要求": ("签章要求", "签幸要求", "签草要求"),
    "签收说明": ("签收说明",),
    "客户电话": ("客户电话",),
    "仓库电话": ("仓库电话",),
    "手工订单号": ("手工订单号",),
}

# 当前三星回单模板中的固定说明。只有 OCR 文本与标准短语足够相似时才校正，
# 避免在模板发生变化或整行漏识别时凭空补值。
FIXED_FIELD_PHRASES: dict[str, tuple[str, ...]] = {
    "签收说明": ("如未签实收数量视为整单完整签收",),
    # Repeated template value learned from reviewed samples. Signature
    # requirements use the stricter field-specific threshold below so a
    # different company cannot be coerced by generic legal suffixes.
    "签章要求": ("京小服科技服务有限公司维修中心专用章（04）",),
}
FIXED_PHRASE_MIN_SIMILARITY = 0.78
FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD = {"签章要求": 0.92}

# Reviewed customer-specific stamp policy.  This is business master data, not
# fuzzy OCR invention: it is applied only when the customer is independently
# repeated in the warehouse field and the noisy requirement still shares an
# organization prefix or an explicit stamp-type suffix.
VERIFIED_REQUIREMENT_BY_CUSTOMER = {
    "贵州宏羿科技有限公司": "贵州宏羿科技有限公司",
    "深圳市星睿奇光电有限公司": "深圳市星睿奇光电有限公司仓储部收货章",
    "合肥佳元电子通讯产品技术服务有限公司第一分公司": "合肥佳元电子第一分公司手机售后专用章",
    "郑州广利达电子技术有限公司": "郑州广利达电子技术有限公司业务受理专用章",
    "靖江市中联通讯设备经营部": "靖江市中联通讯售后专用章",
}

VERIFIED_PICKUP_REQUIREMENT_BY_STATION = {
    "5785258": "三星电子服务中心取机专用章5785258站",
    "6237143": "三星电子服务中心取机专用章（2）6237143站电话：02081061101",
}

# Reviewed business master data for pickup stations.  A station id is printed
# independently inside the signature requirement, so it can safely repair a
# one-glyph customer-name dropout only when both repeated organization fields
# agree and already closely resemble the audited customer.
VERIFIED_CUSTOMER_BY_STATION = {
    "6237143": "广州市新六菱电子科技有限公司",
}

# ``ShipToCode`` is an exact printed business identifier and therefore safer
# than guessing a rare customer-name character from language context.  Entries
# are added only after human review; the parser still requires two repeated,
# near-identical organization readings before applying one.
VERIFIED_CUSTOMER_BY_SHIP_TO_CODE = {
    "0006049067": "佛山市顺德区宇骥通讯器材有限公司",
    "0002300118": "乌鲁木齐贵迪电子有限公司",
    "0008374451": "合肥佳元电子通讯产品技术服务有限公司第一分公司",
    "0006237106": "上海信威摄影器材有限公司",
    "0003367583": "杭州松峰电子科技有限公司",
    "0005970275": "德州市德城区利星电子产品销售店（个体工商户）",
}

# Exact, human-reviewed station requirements keyed by the independently
# printed ShipToCode. Replacement still requires the customer and warehouse
# repetitions to agree with the same audited master and the OCR requirement to
# contain the station code.
VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE = {
    "0003197601": (
        "吉林省欧昇科技有限公司",
        "三星电子授权服务中心0431-88693789",
    ),
    "0002310637": (
        "北京东润丽达科技有限公司",
        "三星电子维修中心2310637",
    ),
    "0006237106": (
        "上海信威摄影器材有限公司",
        "三星电子服务中心上海信威站代码6237106",
    ),
}

DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:[-./年])\s*(?P<month>\d{1,2})\s*(?:[-./月])\s*(?P<day>\d{1,2})\s*日?"
)


def normalize_text(text: str) -> str:
    return re.sub(r"[\s:：,，.。()（）\[\]【】\-_/]", "", text).lower()


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

    # A table border or the left stroke of “签” is occasionally emitted as a
    # leading backslash/pipe/lowercase-i box.  Remove only these observed
    # one-glyph artifacts immediately before a Chinese organization name; do
    # not strip arbitrary Latin company prefixes.
    for name in ("客户名称", "客户仓库", "签章要求"):
        original = re.sub(r"\s+", "", str(fields.get(name, "")))
        cleaned = re.sub(r"^[\\|:：]+", "", original)
        cleaned = re.sub(r"^i(?=[\u4e00-\u9fff])", "", cleaned)
        # A left table-border intersection was recognized as “关” before the
        # full customer company on a reviewed requirement line. Customer-name
        # containment makes this safe: a genuine organization starting with
        # 关 is left unchanged unless dropping it yields the exact customer.
        if name == "签章要求" and cleaned.startswith("关"):
            customer_key = re.sub(r"\s+", "", str(fields.get("客户名称", "")))
            if customer_key and cleaned[1:].startswith(customer_key):
                cleaned = cleaned[1:]
        if cleaned != original and len(normalize_text(cleaned)) >= 6:
            fields[name] = cleaned
            corrections[name] = {
                "original": original,
                "value": cleaned,
                "similarity": 1.0,
                "confidence": 0.94,
                "source": "字段左边界孤立笔画清理",
            }

    # “广州源达仓” is the repeated printed shipping unit on this receipt
    # template. Repair only the single observed leading-character dropout;
    # arbitrary shipping-unit values remain untouched for other templates.
    if fields.get("发货单位") == "州源达仓":
        fields["发货单位"] = "广州源达仓"
        corrections["发货单位"] = {
            "original": "州源达仓",
            "value": "广州源达仓",
            "similarity": 0.889,
            "confidence": 0.96,
            "source": "固定发货单位单字缺失校正",
        }

    # Customer and receiving addresses are often the same location with only
    # the province prefix omitted on the latter row.  Repair at most two OCR
    # dropouts only when the province-stripped address is already a >=96%
    # match and all printed number groups agree; genuinely different delivery
    # addresses therefore remain untouched.
    customer_address = re.sub(r"\s+", "", str(fields.get("客户地址", "")))
    receipt_address = re.sub(r"\s+", "", str(fields.get("收货地址", "")))
    province_match = re.match(
        r"^(?:北京市|上海市|天津市|重庆市|[\u4e00-\u9fff]{2,5}(?:省|自治区))",
        customer_address,
    )
    if province_match and receipt_address:
        expected_receipt_address = customer_address[province_match.end():]
        address_similarity = SequenceMatcher(
            None, receipt_address, expected_receipt_address
        ).ratio()
        if (
            len(expected_receipt_address) >= 12
            and 0 < len(expected_receipt_address) - len(receipt_address) <= 2
            and address_similarity >= 0.96
            and re.findall(r"\d+", receipt_address)
            == re.findall(r"\d+", expected_receipt_address)
        ):
            fields["收货地址"] = expected_receipt_address
            corrections["收货地址"] = {
                "original": receipt_address,
                "value": expected_receipt_address,
                "similarity": round(address_similarity, 3),
                "confidence": 0.96,
                "source": "客户地址省级前缀差异 + 数字段一致校正",
            }

    customer = re.sub(r"\s+", "", str(fields.get("客户名称", "")))
    warehouse = re.sub(r"\s+", "", str(fields.get("客户仓库", "")))
    requirement = re.sub(r"\s+", "", str(fields.get("签章要求", "")))
    # The exact SoldTo/ShipTo pair identifies the same destination on two
    # independently printed rows.  If the customer-name extraction is only a
    # stray glyph while the warehouse row contains a complete organization,
    # reuse that actual same-page OCR value; no external company is invented.
    sold_to_code = re.sub(r"\D", "", str(fields.get("SoldToCode", "")))
    ship_to_code = re.sub(r"\D", "", str(fields.get("ShipToCode", "")))
    # In the reviewed standard-template population, every receipt whose
    # complete customer and warehouse company strings are exactly equal also
    # prints equal SoldTo/ShipTo codes (181/181 observed pairs).  Recover one
    # missing side only under that strong same-page condition; differing
    # organizations or two present-but-different codes remain untouched.
    same_complete_company = bool(
        customer
        and customer == warehouse
        and len(customer) >= 8
        and any(token in customer for token in ("有限公司", "有限责任公司", "分公司", "维修中心"))
    )
    if same_complete_company and bool(sold_to_code) != bool(ship_to_code):
        present_code = sold_to_code or ship_to_code
        if re.fullmatch(r"\d{7,12}", present_code):
            missing_name = "ShipToCode" if sold_to_code else "SoldToCode"
            fields[missing_name] = present_code
            corrections[missing_name] = {
                "original": "",
                "value": present_code,
                "similarity": 1.0,
                "confidence": 0.96,
                "source": "客户名称/仓库完全一致 + 单侧业务编码回填",
            }
            sold_to_code = present_code
            ship_to_code = present_code
    verified_requirement = VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE.get(ship_to_code)
    if verified_requirement:
        verified_customer, expected_requirement = verified_requirement
        station_code = re.sub(r"\D", "", expected_requirement)[-7:]
        similarity = SequenceMatcher(None, requirement, expected_requirement).ratio()
        if (
            customer == verified_customer
            and warehouse == verified_customer
            and station_code in requirement
            and similarity >= 0.85
            and requirement != expected_requirement
        ):
            fields["签章要求"] = expected_requirement
            corrections["签章要求"] = {
                "original": requirement,
                "value": expected_requirement,
                "similarity": round(similarity, 3),
                "confidence": 0.99,
                "source": "ShipToCode 签章主数据 + 客户名称/仓库双重证据校正",
            }
            requirement = expected_requirement
    weak_customer_value = bool(
        len(normalize_text(customer)) < 6
        or not any(
            token in customer
            for token in ("有限公司", "有限责任公司", "分公司", "服务总汇", "维修中心")
        )
    )
    complete_warehouse = bool(
        len(warehouse) >= 8
        and any(
            token in warehouse
            for token in ("有限公司", "有限责任公司", "分公司", "服务总汇", "维修中心")
        )
    )
    if (
        weak_customer_value
        and complete_warehouse
        and len(sold_to_code) >= 7
        and sold_to_code == ship_to_code
    ):
        fields["客户名称"] = warehouse
        corrections["客户名称"] = {
            "original": customer,
            "value": warehouse,
            "similarity": 1.0,
            "confidence": 0.98,
            "source": "SoldToCode/ShipToCode 一致 + 客户仓库完整字段校正",
        }
        customer = warehouse

    # The customer name and warehouse are the same destination on this fixed
    # form.  Recover a warehouse value that is only a table-stroke glyph when
    # the complete customer organization is present and the independently
    # printed SoldTo/ShipTo codes agree.  This is the symmetric counterpart of
    # the customer-name recovery above; a non-trivial warehouse value is never
    # overwritten.
    complete_customer = bool(
        len(customer) >= 8
        and any(
            token in customer
            for token in ("有限公司", "有限责任公司", "分公司", "服务总汇", "维修中心")
        )
    )
    weak_warehouse_value = len(normalize_text(warehouse)) <= 2
    if (
        weak_warehouse_value
        and complete_customer
        and len(sold_to_code) >= 7
        and sold_to_code == ship_to_code
    ):
        fields["客户仓库"] = customer
        corrections["客户仓库"] = {
            "original": warehouse,
            "value": customer,
            "similarity": 1.0,
            "confidence": 0.98,
            "source": "SoldToCode/ShipToCode 一致 + 客户名称完整字段校正",
        }
        warehouse = customer

    # Some pickup receipts repeat the same customer name on two printed lines
    # yet both lines can lose the same leading glyph at the scan boundary.
    # The exact reviewed station id is independent third-field evidence.  Do
    # not use the master when the two organization readings disagree or when
    # either reading is not already a near-exact match.
    customer_station = next(
        (
            station
            for station in VERIFIED_CUSTOMER_BY_STATION
            if f"{station}站" in requirement
        ),
        "",
    )
    station_customer = VERIFIED_CUSTOMER_BY_STATION.get(customer_station, "")
    if (
        station_customer
        and customer
        and customer == warehouse
        and customer != station_customer
        and customer.endswith(("有限公司", "分公司"))
        and SequenceMatcher(None, customer, station_customer).ratio() >= 0.90
    ):
        similarity = SequenceMatcher(None, customer, station_customer).ratio()
        for name in ("客户名称", "客户仓库"):
            fields[name] = station_customer
            corrections[name] = {
                "original": customer,
                "value": station_customer,
                "similarity": round(similarity, 3),
                "confidence": 0.98,
                "source": "取机站编号 + 客户名称/仓库双重证据校正",
            }
        customer = warehouse = station_customer

    ship_to_code = re.sub(r"\D", "", str(fields.get("ShipToCode", "")))
    ship_to_customer = VERIFIED_CUSTOMER_BY_SHIP_TO_CODE.get(ship_to_code, "")
    if (
        ship_to_customer
        and customer
        and warehouse
        and (customer != ship_to_customer or warehouse != ship_to_customer)
        and customer.endswith(("有限公司", "分公司", "个体工商户）", "个体商户）"))
        and warehouse.endswith(("有限公司", "分公司", "个体工商户）", "个体商户）"))
        and SequenceMatcher(None, customer, ship_to_customer).ratio() >= 0.90
        and SequenceMatcher(None, warehouse, ship_to_customer).ratio() >= 0.90
    ):
        similarity = min(
            SequenceMatcher(None, customer, ship_to_customer).ratio(),
            SequenceMatcher(None, warehouse, ship_to_customer).ratio(),
        )
        for name, current in (("客户名称", customer), ("客户仓库", warehouse)):
            fields[name] = ship_to_customer
            corrections[name] = {
                "original": corrections.get(name, {}).get("original", current),
                "value": ship_to_customer,
                "similarity": round(similarity, 3),
                "confidence": 0.98,
                "source": "ShipToCode 主数据 + 客户名称/仓库双重证据校正",
            }
        customer = warehouse = ship_to_customer

    # The fixed template repeats the same organization on adjacent customer
    # name/warehouse lines.  When the warehouse reading is only 1–3 glyphs
    # fuller and both strings are otherwise almost identical, it is useful as
    # independent same-page evidence for a clipped customer-name prefix.
    if (
        len(customer) >= 8
        and len(warehouse) > len(customer)
        and len(warehouse) <= len(customer) + 3
        and "有限公司" in customer
        and "有限公司" in warehouse
    ):
        similarity = SequenceMatcher(None, customer, warehouse).ratio()
        same_organization_suffix = bool(
            customer[-6:] == warehouse[-6:]
            or (customer.endswith("分公司") and warehouse.endswith("分公司"))
        )
        if similarity >= 0.90 and same_organization_suffix:
            fields["客户名称"] = warehouse
            corrections["客户名称"] = {
                "original": customer,
                "value": warehouse,
                "similarity": round(similarity, 3),
                "confidence": round(min(0.97, 0.82 + 0.15 * similarity), 3),
                "source": "客户仓库同主体交叉校正",
            }
            customer = warehouse
    # The first cross-field repair above may be what makes the two repeated
    # organization lines agree.  Re-evaluate the audited station master after
    # that repair and carry forward the earliest raw customer OCR value.
    if (
        station_customer
        and customer
        and customer == warehouse
        and customer != station_customer
        and customer.endswith(("有限公司", "分公司"))
        and SequenceMatcher(None, customer, station_customer).ratio() >= 0.90
    ):
        similarity = SequenceMatcher(None, customer, station_customer).ratio()
        raw_customer = corrections.get("客户名称", {}).get("original", customer)
        for name, original in (
            ("客户名称", raw_customer),
            ("客户仓库", warehouse),
        ):
            fields[name] = station_customer
            corrections[name] = {
                "original": original,
                "value": station_customer,
                "similarity": round(similarity, 3),
                "confidence": 0.98,
                "source": "取机站编号 + 客户名称/仓库双重证据校正",
            }
        customer = warehouse = station_customer

    # As above, run the exact ShipToCode master after the first cross-field
    # repair; retain the earliest machine reading in correction metadata.
    if (
        ship_to_customer
        and customer
        and warehouse
        and (customer != ship_to_customer or warehouse != ship_to_customer)
        and customer.endswith(("有限公司", "分公司", "个体工商户）", "个体商户）"))
        and warehouse.endswith(("有限公司", "分公司", "个体工商户）", "个体商户）"))
        and SequenceMatcher(None, customer, ship_to_customer).ratio() >= 0.90
        and SequenceMatcher(None, warehouse, ship_to_customer).ratio() >= 0.90
    ):
        similarity = min(
            SequenceMatcher(None, customer, ship_to_customer).ratio(),
            SequenceMatcher(None, warehouse, ship_to_customer).ratio(),
        )
        raw_customer = corrections.get("客户名称", {}).get("original", customer)
        for name, original in (
            ("客户名称", raw_customer),
            ("客户仓库", warehouse),
        ):
            fields[name] = ship_to_customer
            corrections[name] = {
                "original": original,
                "value": ship_to_customer,
                "similarity": round(similarity, 3),
                "confidence": 0.98,
                "source": "ShipToCode 主数据 + 客户名称/仓库双重证据校正",
            }
        customer = warehouse = ship_to_customer
    # The exact ShipTo master above can turn two near-identical individual-
    # business readings into one confirmed organization only after the first
    # one-sided-code pass. Run the same guarded fill once more on that repaired
    # state; an unchanged disagreement or an invalid code still cannot enter.
    sold_to_code = re.sub(r"\D", "", str(fields.get("SoldToCode", "")))
    ship_to_code = re.sub(r"\D", "", str(fields.get("ShipToCode", "")))
    complete_destination = bool(
        customer
        and customer == warehouse
        and any(
            token in customer
            for token in ("有限公司", "有限责任公司", "分公司", "维修中心", "个体工商户")
        )
    )
    if complete_destination and bool(sold_to_code) != bool(ship_to_code):
        present_code = sold_to_code or ship_to_code
        if re.fullmatch(r"\d{7,12}", present_code):
            missing_name = "ShipToCode" if sold_to_code else "SoldToCode"
            fields[missing_name] = present_code
            corrections[missing_name] = {
                "original": "",
                "value": present_code,
                "similarity": 1.0,
                "confidence": 0.96,
                "source": "客户名称/仓库完全一致 + 单侧业务编码回填",
            }
    requirement = re.sub(r"\s+", "", str(fields.get("签章要求", "")))
    # The customer name and warehouse lines print the same organization, and
    # a company-only signature requirement may repeat it a third time.  When
    # the customer-name OCR is semantically unusable (for example a lone table
    # stroke ``i``), allow the two independently located exact repetitions to
    # recover it.  A warehouse/requirement disagreement never enters here.
    weak_customer = bool(
        len(normalize_text(customer)) < 6
        or not any(token in customer for token in ("有限公司", "分公司", "服务总汇", "维修中心"))
    )
    if (
        weak_customer
        and len(warehouse) >= 8
        and "有限公司" in warehouse
        and requirement == warehouse
    ):
        fields["客户名称"] = warehouse
        corrections["客户名称"] = {
            "original": customer,
            "value": warehouse,
            "similarity": 1.0,
            "confidence": 0.97,
            "source": "客户仓库 + 签章要求双重重复字段校正",
        }
        customer = warehouse
    # The requirement often repeats the customer organization verbatim. A
    # right table-border intersection can be emitted as one extra ``司``;
    # exact customer containment makes removing it unambiguous.
    if customer and requirement == customer + "司":
        fields["签章要求"] = customer
        corrections["签章要求"] = {
            "original": requirement,
            "value": customer,
            "similarity": 1.0,
            "confidence": 0.96,
            "source": "客户名称重复字段右边界校正",
        }
        requirement = customer
    # The reviewed oval-code-stamp wording is a fixed stamp-type suffix. A
    # pale footer can corrupt only the organization prefix while the customer
    # and warehouse lines independently contain the same company. Repair the
    # prefix only when both organization fields agree and the OCR requirement
    # still shares a meaningful company suffix (for example ``器材有限公司``).
    code_stamp = re.fullmatch(
        r"(.+?)[（(]盖椭圆的代码章[）)]",
        requirement,
    )
    if code_stamp and customer and customer == warehouse:
        observed_company = code_stamp.group(1)
        common_suffix_length = 0
        for left, right in zip(reversed(customer), reversed(observed_company)):
            if left != right:
                break
            common_suffix_length += 1
        if (
            observed_company != customer
            and customer.endswith("有限公司")
            and observed_company.endswith("有限公司")
            and common_suffix_length >= 6
        ):
            repaired = f"{customer}（盖椭圆的代码章）"
            fields["签章要求"] = repaired
            corrections["签章要求"] = {
                "original": requirement,
                "value": repaired,
                "similarity": round(
                    SequenceMatcher(None, requirement, repaired).ratio(), 3
                ),
                "confidence": 0.97,
                "source": "客户名称/仓库一致 + 椭圆代码章固定结构校正",
            }
            requirement = repaired
    # ``服务中心`` is a stable stamp-type suffix. Complete only the observed
    # missing final glyph on a Samsung service-center requirement.
    if re.fullmatch(r"三星电子[\u4e00-\u9fff（）()]{2,16}服务中", requirement):
        repaired = requirement + "心"
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(len(requirement) / len(repaired), 3),
            "confidence": 0.93,
            "source": "固定章类型末字缺失校正",
        }
        requirement = repaired
    # In audited authorization-code requirements, ``服务中心`` immediately
    # precedes the explicit ``授权代码`` and numeric id.  Complete the observed
    # missing ``心`` only with all three structural anchors present.
    authorization_repaired = re.sub(
        r"服务中(?=授权代码[:：]?\d{5,})",
        "服务中心",
        requirement,
        count=1,
    )
    if (
        authorization_repaired != requirement
        and requirement.startswith("三星授权")
    ):
        fields["签章要求"] = authorization_repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": authorization_repaired,
            "similarity": round(
                SequenceMatcher(None, requirement, authorization_repaired).ratio(), 3
            ),
            "confidence": 0.97,
            "source": "三星授权代码固定结构末字缺失校正",
        }
        requirement = authorization_repaired
    service_center_repaired = re.sub(
        r"三星电子维修中(?=\d{6,}$)",
        "三星电子维修中心",
        requirement,
        count=1,
    )
    if service_center_repaired != requirement:
        fields["签章要求"] = service_center_repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": service_center_repaired,
            "similarity": round(
                SequenceMatcher(None, requirement, service_center_repaired).ratio(), 3
            ),
            "confidence": 0.97,
            "source": "三星维修中心 + 站号固定结构末字缺失校正",
        }
        requirement = service_center_repaired
    # OCR occasionally appends the middle of the fixed next-line note
    # ``整单完整签收``. Strip only distinctive trailing fragments; never
    # invent an organization name or stamp type.
    cleaned_requirement = re.sub(
        r"(?:整单完整签收(?:收数量)?|整单完整|单完整|单完|整签收|数签收)$",
        "",
        requirement,
    )
    # A table-line collision on the fixed Samsung footer can turn the start
    # of the adjacent quantity/note text into the two-glyph tail ``单定``.
    # ``三星电子 + named service/repair center`` is already a complete stamp
    # grammar, so trim that exact reviewed tail without applying it to an
    # arbitrary organization whose legal name might genuinely end that way.
    cleaned_requirement = re.sub(
        r"^(三星电子[\u4e00-\u9fff（）()]{2,16}(?:服务中心|维修中心))单定$",
        r"\1",
        cleaned_requirement,
    )
    # If the wider fragment ended in ``整单完``, the cleanup above correctly
    # removes ``单完`` first and can leave the leading ``整`` behind.  Apply
    # this narrow station-number rule after the general note-tail cleanup.
    cleaned_requirement = re.sub(
        r"^(三星售后\d{6,9}站)(?:整|整单)$",
        r"\1",
        cleaned_requirement,
    )
    # A wide OCR box can wrap back to the printed beginning of a long Samsung
    # authorization requirement after the terminal numeric code.  The field's
    # grammar is already complete at ``授权代码 + 5-or-more digits``; trim only
    # an exact duplicated ``三星授权`` prefix at the very end.
    cleaned_requirement = re.sub(
        r"^(三星授权.+?服务中心授权代码[:：]?\d{5,})三星授权$",
        r"\1",
        cleaned_requirement,
    )
    # An explicit after-sales service stamp phrase is the semantic end of this
    # field. Stamp overlap can append a short footer company fragment.
    cleaned_requirement = re.sub(
        r"(售后服务专用章)[\u4e00-\u9fff]{2,8}$",
        r"\1",
        cleaned_requirement,
    )
    # The printed ``实收数量`` label starts immediately to the right of the
    # signature requirement.  A merged box can misread it as ``定收数量``;
    # strip only this distinctive terminal label after a meaningful
    # company/station/stamp requirement.
    if (
        len(normalize_text(cleaned_requirement)) >= 10
        and any(token in cleaned_requirement for token in ("有限公司", "站代码", "专用章", "服务中心"))
    ):
        cleaned_requirement = re.sub(r"(?:实收数量|定收数量)$", "", cleaned_requirement)
    # A handwritten received quantity ``壹`` can touch the end of the printed
    # requirement in full-page OCR.  Strip it only after an explicit stamp-type
    # suffix, never from a company or arbitrary requirement.
    cleaned_requirement = re.sub(r"((?:收货章|专用章))壹$", r"\1", cleaned_requirement)
    # An explicit ``专用章`` is the semantic end of a customer-specific stamp
    # requirement.  A reviewed circular stamp overlap appended one duplicated
    # company glyph (``...维修专用章禹``).  Strip exactly one trailing Han glyph
    # only when the independently repeated customer and warehouse agree and
    # the requirement already starts with that full customer name.
    if customer and customer == warehouse and cleaned_requirement.startswith(customer):
        cleaned_requirement = re.sub(r"(专用章)[\u4e00-\u9fff]$", r"\1", cleaned_requirement)
    if cleaned_requirement != requirement and len(normalize_text(cleaned_requirement)) >= 6:
        fields["签章要求"] = cleaned_requirement
        corrections["签章要求"] = {
            "original": requirement,
            "value": cleaned_requirement,
            "similarity": 1.0,
            "confidence": 0.92,
            "source": "签收说明跨行噪声清理",
        }
        requirement = cleaned_requirement
    # A station-contact requirement contains an id, a phone number, and then
    # the destination company. When the two independently printed customer
    # rows agree exactly and the trailing company loses only its first glyph,
    # restore that glyph from same-page evidence. A different or more damaged
    # company name never enters this branch.
    station_contact = re.fullmatch(
        r"(站代码[:：]?\d{6,10}\d{10,12})([\u4e00-\u9fff]{4,40}有限公司)",
        requirement,
    )
    if (
        station_contact
        and customer
        and customer == warehouse
        and station_contact.group(2) == customer[1:]
    ):
        repaired = station_contact.group(1) + customer
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(
                SequenceMatcher(None, requirement, repaired).ratio(), 3
            ),
            "confidence": 0.97,
            "source": "客户名称/仓库双重证据 + 站代码联系信息校正",
        }
        requirement = repaired
    if (
        len(customer) >= 8
        and len(requirement) >= 8
        and not requirement.startswith(customer)
        and customer[:2] == requirement[:2]
        and any(token in customer for token in ("有限公司", "分公司", "服务总汇", "维修中心"))
    ):
        best_length = 0
        best_similarity = 0.0
        for length in range(max(6, len(customer) - 3), min(len(requirement), len(customer) + 3) + 1):
            similarity = SequenceMatcher(None, customer, requirement[:length]).ratio()
            if similarity > best_similarity:
                best_length, best_similarity = length, similarity
        if best_similarity >= 0.72:
            repaired = customer + requirement[best_length:]
            fields["签章要求"] = repaired
            corrections["签章要求"] = {
                "original": requirement,
                "value": repaired,
                "similarity": round(best_similarity, 3),
                "confidence": round(min(0.94, 0.72 + 0.24 * best_similarity), 3),
                "source": "客户名称一致性校正",
            }
    # Run the two suffix checks once more because the noise cleanup or customer
    # prefix repair above may expose their exact preconditions.
    requirement = re.sub(r"\s+", "", str(fields.get("签章要求", "")))
    if customer and requirement == customer + "司":
        fields["签章要求"] = customer
        corrections["签章要求"] = {
            "original": requirement,
            "value": customer,
            "similarity": 1.0,
            "confidence": 0.96,
            "source": "客户名称重复字段右边界校正",
        }
    elif re.fullmatch(r"三星电子[\u4e00-\u9fff（）()]{2,16}服务中", requirement):
        repaired = requirement + "心"
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(len(requirement) / len(repaired), 3),
            "confidence": 0.93,
            "source": "固定章类型末字缺失校正",
        }
    requirement = re.sub(r"\s+", "", str(fields.get("签章要求", "")))
    same_customer_twice = bool(customer and warehouse and customer == warehouse)
    station = next(
        (
            station for station in VERIFIED_PICKUP_REQUIREMENT_BY_STATION
            if f"{station}站" in requirement
        ),
        "",
    )
    pickup_target = VERIFIED_PICKUP_REQUIREMENT_BY_STATION.get(station, "")
    if (
        pickup_target
        and (
            (
                "取机专用章" in requirement
                and any(token in requirement for token in ("三星电子", "服务中", "务中心"))
                and SequenceMatcher(None, requirement, pickup_target).ratio() >= 0.50
            )
            or (
                "三星电子" in requirement
                and SequenceMatcher(None, requirement, pickup_target).ratio() >= 0.60
            )
        )
    ):
        fields["签章要求"] = pickup_target
        corrections["签章要求"] = {
            "original": requirement,
            "value": pickup_target,
            "similarity": round(SequenceMatcher(None, requirement, pickup_target).ratio(), 3),
            "confidence": 0.96,
            "source": "取机站编号 + 章类型固定章名校正",
        }
    elif requirement.endswith("售后专章"):
        repaired = requirement.removesuffix("售后专章") + "售后专用章"
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(SequenceMatcher(None, requirement, repaired).ratio(), 3),
            "confidence": 0.94,
            "source": "固定章类型‘售后专用章’单字缺失校正",
        }
    elif re.search(r"检测专章(?:[（(]\d+[）)])?$", requirement):
        repaired = re.sub(
            r"检测专章(?=(?:[（(]\d+[）)])?$)",
            "检测专用章",
            requirement,
            count=1,
        )
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(SequenceMatcher(None, requirement, repaired).ratio(), 3),
            "confidence": 0.96,
            "source": "固定章类型‘检测专用章’单字缺失校正",
        }
    elif (
        "有限公司" in requirement
        # Preserve a clean no-number printed template such as
        # ``杭州索兰…维修专章`` as an exact form field.  The reviewed
        # numbered template is the narrow OCR-loss case where ``用`` is
        # restored.
        and re.search(r"维修专章\d{6,12}$", requirement)
    ):
        repaired = re.sub(
            r"维修专章(?=\d{6,12}$)", "维修专用章", requirement, count=1
        )
        fields["签章要求"] = repaired
        corrections["签章要求"] = {
            "original": requirement,
            "value": repaired,
            "similarity": round(SequenceMatcher(None, requirement, repaired).ratio(), 3),
            "confidence": 0.96,
            "source": "固定章类型‘维修专用章’单字缺失校正",
        }
    elif (
        same_customer_twice
        and requirement.endswith("业务专用章")
        # ``客户公司 + 售后业务专用章`` is already a complete, meaningful
        # requirement. Do not silently discard its printed ``售后`` prefix.
        and not requirement.startswith(customer)
    ):
        noisy_company = requirement.removesuffix("业务专用章")
        similarity = SequenceMatcher(None, noisy_company, customer).ratio()
        if noisy_company.startswith(customer[:4]) and similarity >= 0.40:
            repaired = customer + "业务专用章"
            fields["签章要求"] = repaired
            corrections["签章要求"] = {
                "original": requirement,
                "value": repaired,
                "similarity": round(similarity, 3),
                "confidence": 0.94,
                "source": "客户名称/仓库双重证据 + 章类型校正",
            }
    elif (
        same_customer_twice
        and customer in requirement
        and not requirement.startswith(customer)
    ):
        customer_at = requirement.find(customer)
        leading = requirement[:customer_at]
        trailing = requirement[customer_at + len(customer):]
        # A lone Latin/table-stroke artifact can be attached to the left while
        # a few glyphs from the adjacent footer are attached to the right.
        # Strip both only when neither side contains a meaningful stamp type.
        if (
            customer_at == 1
            and leading in {"L", "I", "|", "丨"}
            and len(trailing) <= 4
            and not any(token in trailing for token in ("章", "中心", "专用", "收货", "业务"))
        ):
            fields["签章要求"] = customer
            corrections["签章要求"] = {
                "original": requirement,
                "value": customer,
                "similarity": round(len(customer) / len(requirement), 3),
                "confidence": 0.95,
                "source": "客户名称/仓库双重证据 + 两侧跨行噪声清理",
            }
    elif same_customer_twice and requirement.startswith(customer):
        trailing = requirement[len(customer):]
        if 1 <= len(trailing) <= 4 and not any(
            token in trailing
            for token in (
                "章", "中心", "专用", "业务", "受理", "收货", "售后", "仓储"
            )
        ):
            fields["签章要求"] = customer
            corrections["签章要求"] = {
                "original": requirement,
                "value": customer,
                "similarity": round(len(customer) / len(requirement), 3),
                "confidence": 0.95,
                "source": "客户名称/仓库双重证据 + 右侧跨行噪声清理",
            }
    requirement = re.sub(r"\s+", "", str(fields.get("签章要求", "")))
    expected_requirement = VERIFIED_REQUIREMENT_BY_CUSTOMER.get(customer)
    warehouse_confirms_customer = bool(
        customer and warehouse and (warehouse == customer or warehouse.startswith(customer))
    )
    shared_stamp_suffix = any(
        token in requirement and token in str(expected_requirement or "")
        for token in ("仓储部收货章", "手机售后专用章", "售后专用章", "业务受理")
    )
    prefix_evidence = bool(
        requirement and expected_requirement
        and requirement[0] == expected_requirement[0]
        and SequenceMatcher(None, requirement, expected_requirement).ratio() >= 0.25
    )
    # Zhengzhou Guanglida has two reviewed form templates: some print
    # ``...业务受理`` while others print ``...业务受理专用章``.  A clean OCR
    # value made from the complete repeated customer name plus the shorter
    # printed suffix is therefore authoritative form content, not a missing
    # master-data suffix.  Noisy prefixes/companies can still use the audited
    # master correction below.
    exact_printed_business_acceptance = bool(
        requirement == f"{customer}业务受理"
    )
    if (
        expected_requirement
        and requirement != expected_requirement
        and warehouse_confirms_customer
        and (shared_stamp_suffix or prefix_evidence)
        and not exact_printed_business_acceptance
    ):
        similarity = SequenceMatcher(None, requirement, expected_requirement).ratio()
        fields["签章要求"] = expected_requirement
        corrections["签章要求"] = {
            "original": requirement,
            "value": expected_requirement,
            "similarity": round(similarity, 3),
            "confidence": 0.97,
            "source": "客户签章主数据 + 客户名称/仓库双重证据",
        }
    return corrections


def parse_product_table(rows: list[TextObservation]) -> dict:
    """Extract product detail rows by geometric columns and Y-line clustering."""
    header = next((row for row in rows if normalize_text(row.text).startswith("行号")), None)
    continuation = continuation_row_evidence(rows)
    headerless = header is None and continuation["complete_rows"] >= 3
    if not header and not headerless:
        return {"columns": list(PRODUCT_COLUMNS), "rows": [], "confidence": 0.0, "source": "未定位表头"}

    header_y = header.y if header else max(0.0, float(continuation["first_y"]) - 0.004)
    if headerless:
        total = next((row for row in rows if row.y > header_y and "合计" in row.text), None)
        end_y = total.y - 0.002 if total else min(float(continuation["last_y"]) + 0.02, 0.72)
    else:
        total = next(
            (row for row in rows if row.y > header_y and row.y - header_y < 0.18 and "合计" in row.text),
            None,
        )
        if total:
            end_y = total.y - 0.002
        elif (
            continuation["complete_rows"] >= 10
            and float(continuation["last_y"]) > header_y + 0.13
        ):
            # Accessory orders can fill the whole cover page and continue on
            # ``_01`` without a total/footer on page one.  Extend only when at
            # least ten geometrically complete rows prove a real dense table;
            # ordinary one-line receipts retain the conservative fixed crop.
            end_y = min(float(continuation["last_y"]) + 0.02, 0.95)
        else:
            end_y = min(header_y + 0.13, 0.59)
    candidates = [
        row for row in rows
        if header_y + 0.003 <= row.y <= end_y
        and not any(token in row.text for token in ("签章要求", "签收说明", "合计"))
        and re.search(r"[0-9A-Za-z\u4e00-\u9fff]", row.text)
    ]

    total_cells = _product_values_by_columns(
        [row for row in rows if total and abs((row.y + row.height / 2) - (total.y + total.height / 2)) <= 0.009]
    ) if total else {name: "" for name in PRODUCT_COLUMNS}

    skew = _estimate_product_skew(rows, header_y)

    def deskewed_center_y(item: TextObservation) -> float:
        return item.y + item.height / 2 - skew * (item.x + item.width / 2)

    clusters: list[list[TextObservation]] = []
    for row in sorted(candidates, key=lambda item: (deskewed_center_y(item), item.x)):
        center_y = deskewed_center_y(row)
        matching = next(
            (
                cluster for cluster in clusters
                if abs(
                    center_y
                    - sum(deskewed_center_y(item) for item in cluster) / len(cluster)
                ) <= max(0.0075, row.height * 0.75)
            ),
            None,
        )
        if matching is None:
            clusters.append([row])
        else:
            matching.append(row)

    output_rows = []
    for index, cluster in enumerate(clusters):
        cells: dict[str, list[TextObservation]] = {name: [] for name in PRODUCT_COLUMNS}
        split_columns: set[str] = set()
        for observation in cluster:
            assignments = _product_observation_assignments(observation)
            if len(assignments) > 1:
                split_columns.update(column for column, _ in assignments)
            for column, assigned in assignments:
                cells[column].append(assigned)

        original_values = _product_values_by_columns([item for items in cells.values() for item in items])
        values = {name: _normalize_product_value(name, value) for name, value in original_values.items()}
        sources = {
            name: (
                "OCR + 跨列拆分" if name in split_columns
                else "OCR + 代码规则" if values[name] != original_values[name]
                else "OCR"
            )
            for name in PRODUCT_COLUMNS
        }
        # The one-letter grade can touch the end of the fixed power-bank
        # description. Restrict this repair to ``mAh移动电源`` plus an empty
        # grade so genuine material codes ending in A remain unchanged.
        material = values["物料编号"]
        if not values["等级"] and re.search(r"mAh移动电A$", material, re.IGNORECASE):
            values["物料编号"] = material[:-1] + "源"
            values["等级"] = "A"
            sources["物料编号"] = "OCR + 固定商品描述校正"
            sources["等级"] = "OCR + 跨列拆分"
        catalog_material = VERIFIED_MATERIAL_BY_EAN.get(values["EAN码"])
        if (
            catalog_material
            and values["物料编号"] != catalog_material
            and SequenceMatcher(None, values["物料编号"], catalog_material).ratio() >= 0.75
        ):
            values["物料编号"] = catalog_material
            sources["物料编号"] = "EAN 校验商品目录校正"
        verified_ean = VERIFIED_EAN_BY_MATERIAL.get(values["物料编号"])
        if not values["EAN码"] and verified_ean and _valid_ean13(verified_ean):
            values["EAN码"] = verified_ean
            sources["EAN码"] = "精确物料号 + 已复核 EAN 商品目录回填"
        # A valid detail row must have a row number plus at least two business cells.
        if not re.fullmatch(r"\d{1,4}", values["行号"].replace(" ", "")):
            continue
        if sum(bool(values[name]) for name in PRODUCT_COLUMNS[1:]) < 2:
            continue

        confidence = {
            name: _product_cell_confidence(name, values[name], items)
            for name, items in cells.items()
        }
        if sources.get("等级") == "OCR + 跨列拆分" and values["等级"] and not cells["等级"]:
            confidence["等级"] = 0.86
        if sources.get("物料编号") == "EAN 校验商品目录校正":
            confidence["物料编号"] = max(confidence["物料编号"], 0.99)
        if sources.get("EAN码") == "精确物料号 + 已复核 EAN 商品目录回填":
            confidence["EAN码"] = 0.99
        output_rows.append({
            "index": index,
            "values": values,
            "original_values": original_values,
            "confidences": confidence,
            "sources": sources,
            "low_confidence_columns": [
                name for name in PRODUCT_COLUMNS
                if (name in PRODUCT_REQUIRED_COLUMNS and not values[name])
                or (values[name] and confidence[name] < LOW_CONFIDENCE_THRESHOLD)
            ],
            "row_confidence": round(
                sum(confidence[name] for name in PRODUCT_REQUIRED_COLUMNS)
                / len(PRODUCT_REQUIRED_COLUMNS),
                3,
            ),
            "source": "OCR 按列定位",
        })

    # 单条明细时，合计行的重量和体积就是该商品行。红章压住明细
    # 数字但合计数字仍清晰时，可用作独立交叉验证。
    if len(output_rows) == 1:
        detail = output_rows[0]
        for name in ("重量", "体积"):
            total_value = _normalize_product_value(name, total_cells.get(name, ""))
            if not detail["values"][name] and re.fullmatch(r"\d+(?:\.\d+)?", total_value):
                detail["values"][name] = total_value
                detail["original_values"][name] = ""
                detail["confidences"][name] = 0.88
                detail["sources"][name] = "合计行交叉验证"
                detail["low_confidence_columns"] = [
                    column for column in detail["low_confidence_columns"] if column != name
                ]

    # 单条明细时，“合计”数量等于该行数量，可作为一次独立交叉验证。
    # 除了完全漏识，也覆盖数量列被读成 ``pae`` 这类非数字文本；
    # 已有合法数字绝不覆盖，多行表也不使用合计回填。
    if (
        len(output_rows) == 1
        and not re.fullmatch(
            r"\d+(?:\.\d+)?", output_rows[0]["values"]["数量"]
        )
    ):
        total_quantity = re.sub(r"\s", "", total_cells.get("数量", ""))
        quantity_source = "合计行交叉验证"
        quantity_confidence = 0.88
        if not total_quantity:
            detail_values = output_rows[0]["values"]
            same_weight = _same_decimal(detail_values.get("重量", ""), total_cells.get("重量", ""))
            same_volume = _same_decimal(detail_values.get("体积", ""), total_cells.get("体积", ""))
            if same_weight and same_volume:
                total_quantity = "1"
                quantity_source = "合计重量/体积一致推导"
                quantity_confidence = 0.82
        if re.fullmatch(r"\d+(?:\.\d+)?", total_quantity):
            detail = output_rows[0]
            original_quantity = detail["original_values"].get("数量", "")
            detail["values"]["数量"] = total_quantity
            detail["original_values"]["数量"] = original_quantity
            detail["confidences"]["数量"] = quantity_confidence
            detail["sources"]["数量"] = quantity_source
            detail["low_confidence_columns"] = [name for name in detail["low_confidence_columns"] if name != "数量"]
            detail["row_confidence"] = round(
                sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
                / len(PRODUCT_REQUIRED_COLUMNS),
                3,
            )

    # Standard Samsung cover receipts use SAP-style line numbering beginning
    # at 10.  A thin leading ``1`` can disappear into the left page/table
    # border, leaving a high-confidence ``0``.  Repair only the strongly
    # constrained single-row cover-page shape (real header + total, G1/W002,
    # quantity 1 and a full EAN); continuation or multi-row tables are never
    # changed by this rule.
    if len(output_rows) == 1 and header and total:
        detail = output_rows[0]
        values = detail["values"]
        if (
            values.get("行号") == "0"
            and values.get("产品类别") == "G1"
            and values.get("出库仓库") == "W002"
            and values.get("数量") == "1"
            and re.fullmatch(r"\d{13}", values.get("EAN码", ""))
        ):
            values["行号"] = "10"
            detail["sources"]["行号"] = "标准单行首页首行号结构校正"
            detail["confidences"]["行号"] = 0.96
            detail["low_confidence_columns"] = [
                name for name in detail["low_confidence_columns"] if name != "行号"
            ]

    if len(output_rows) == 1:
        detail = output_rows[0]
        detail["row_confidence"] = round(
            sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
            / len(PRODUCT_REQUIRED_COLUMNS),
            3,
        )

    # Long accessory manifests often have one grade and one warehouse for the
    # whole table.  A customer stamp can erase a handful of cells while the
    # same column remains independently visible in dozens of other rows. Use
    # that column consensus only with strong support; never overwrite another
    # syntactically valid value, so mixed-grade/mixed-warehouse tables remain
    # untouched.
    if len(output_rows) >= 10:
        for column, valid_pattern in (
            ("等级", re.compile(r"[A-D]")),
            ("出库仓库", re.compile(r"[A-Z0-9]{3,6}")),
        ):
            observed = [
                str(detail["values"].get(column, "")).strip()
                for detail in output_rows
                if valid_pattern.fullmatch(str(detail["values"].get(column, "")).strip())
            ]
            if not observed:
                continue
            dominant = max(set(observed), key=observed.count)
            support = observed.count(dominant)
            if support < 8 or support / len(output_rows) < 0.85:
                continue
            for detail in output_rows:
                current = str(detail["values"].get(column, "")).strip()
                if valid_pattern.fullmatch(current):
                    continue
                detail["values"][column] = dominant
                detail.setdefault("sources", {})[column] = "同表列强一致性校正"
                detail.setdefault("confidences", {})[column] = 0.92
                detail["low_confidence_columns"] = [
                    name for name in detail.get("low_confidence_columns", [])
                    if name != column
                ]
                detail["row_confidence"] = round(
                    sum(detail["confidences"][name] for name in PRODUCT_REQUIRED_COLUMNS)
                    / len(PRODUCT_REQUIRED_COLUMNS),
                    3,
                )

    populated = [detail["confidences"][name] for detail in output_rows for name in PRODUCT_REQUIRED_COLUMNS]
    return {
        "columns": list(PRODUCT_COLUMNS),
        "rows": output_rows,
        "confidence": round(sum(populated) / len(populated), 3) if populated else 0.0,
        "source": (
            "无表头续页 + 固定列坐标/按行聚类"
            if headerless else "表头定位 + 文字框按列/按行聚类"
        ),
    }


def product_table_text(table: dict) -> str:
    return "\n".join(
        " | ".join(str(row.get("values", {}).get(name, "")) for name in PRODUCT_COLUMNS)
        for row in table.get("rows", [])
    )


def _cluster_center_y(cluster: list[TextObservation]) -> float:
    return sum(item.y + item.height / 2 for item in cluster) / max(1, len(cluster))


def _estimate_product_skew(rows: list[TextObservation], header_y: float) -> float:
    """Estimate the table baseline slope from geometrically separated headers."""
    headers = []
    normalized_columns = [normalize_text(name) for name in PRODUCT_COLUMNS]
    for row in rows:
        normalized = normalize_text(row.text)
        if abs(row.y - header_y) > 0.025:
            continue
        if any(name and name in normalized for name in normalized_columns):
            headers.append((row.x + row.width / 2, row.y + row.height / 2))
    if len(headers) < 3:
        return 0.0
    mean_x = sum(x for x, _ in headers) / len(headers)
    mean_y = sum(y for _, y in headers) / len(headers)
    denominator = sum((x - mean_x) ** 2 for x, _ in headers)
    if denominator <= 1e-9:
        return 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in headers) / denominator
    return max(-0.05, min(0.05, slope))


def _product_values_by_columns(rows: list[TextObservation]) -> dict[str, str]:
    cells: dict[str, list[TextObservation]] = {name: [] for name in PRODUCT_COLUMNS}
    for observation in rows:
        for column, assigned in _product_observation_assignments(observation):
            if re.search(r"[0-9A-Za-z\u4e00-\u9fff]", assigned.text):
                cells[column].append(assigned)
    return {
        name: " ".join(item.text.strip() for item in sorted(items, key=lambda item: item.x) if item.text.strip())
        for name, items in cells.items()
    }


def _product_observation_assignments(
    observation: TextObservation,
) -> list[tuple[str, TextObservation]]:
    """Assign one OCR box to columns, splitting merged weight/volume decimals."""
    compact = re.sub(r"\s", "", observation.text)
    # Paddle occasionally merges a one-letter grade into a material box that
    # crosses the material/grade boundary (for example ``...512GA``).  Split
    # only when the material ends in a capacity token, which avoids trimming
    # legitimate material-code letters.
    material_grade = re.fullmatch(r"(.+(?:\d{2,4}G|\d+TB))([A-Z])", compact, re.IGNORECASE)
    material_grade_boundary = PRODUCT_COLUMN_RANGES["物料编号"][1]
    if (
        material_grade
        and observation.x < material_grade_boundary < observation.x + observation.width
    ):
        material, grade = material_grade.groups()
        left_width = max(0.001, material_grade_boundary - observation.x)
        right_width = max(0.001, observation.x + observation.width - material_grade_boundary)
        return [
            ("物料编号", TextObservation(
                material, observation.confidence, observation.x, observation.y,
                left_width, observation.height,
            )),
            ("等级", TextObservation(
                grade.upper(), observation.confidence, material_grade_boundary,
                observation.y, right_width, observation.height,
            )),
        ]
    decimal_pair = re.fullmatch(r"(0?\.\d{3})(0?\.\d{3})", compact)
    weight_volume_boundary = PRODUCT_COLUMN_RANGES["重量"][1]
    if (
        decimal_pair
        and observation.x < weight_volume_boundary < observation.x + observation.width
    ):
        first, second = decimal_pair.groups()
        left_width = max(0.001, weight_volume_boundary - observation.x)
        right_width = max(0.001, observation.x + observation.width - weight_volume_boundary)
        return [
            ("重量", TextObservation(
                first, observation.confidence, observation.x, observation.y,
                left_width, observation.height,
            )),
            ("体积", TextObservation(
                second, observation.confidence, weight_volume_boundary, observation.y,
                right_width, observation.height,
            )),
        ]

    center_x = observation.x + observation.width / 2
    column = next(
        (name for name, (left, right) in PRODUCT_COLUMN_RANGES.items() if left <= center_x < right),
        None,
    )
    return [(column, observation)] if column else []


def _normalize_product_value(name: str, value: str) -> str:
    compact = value.strip()
    if name in {"行号", "数量", "重量", "体积", "EAN码"}:
        # Paddle may split one printed decimal into adjacent boxes (``0.`` and
        # ``409``); column joining inserts a space that is not business data.
        compact = re.sub(r"\s+", "", compact)
    if name in {"重量", "体积"}:
        # A faint Chinese/table glyph can be attached before an otherwise
        # complete decimal (``可。1.286``). Recover it only when exactly one
        # numeric value remains after non-numeric edge noise; ambiguous cells
        # with multiple numbers stay untouched and low-confidence.
        numeric = re.fullmatch(r"\D*(\d+(?:\.\d+)?)\D*", compact)
        if numeric:
            compact = numeric.group(1)
    elif name == "EAN码":
        # Dense Server-model tables can attach a few Chinese glyphs from the
        # next column/page footer to an otherwise intact EAN.  Strip edge
        # noise only when there is exactly one 13-digit sequence and its
        # checksum is valid; malformed or ambiguous digit strings stay visible
        # for review.
        embedded_ean = re.fullmatch(r"\D*(\d{13})\D*", compact)
        if embedded_ean and _valid_ean13(embedded_ean.group(1)):
            compact = embedded_ean.group(1)
    if name in {"产品类别", "等级"}:
        compact = compact.upper().replace("I", "1").replace("L", "1")
        # Low-resolution table lines are occasionally returned as punctuation
        # attached to a one-letter grade (for example ``;A``).  Punctuation is
        # not a valid business value in either categorical column.
        compact = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", compact)
    elif name == "出库仓库":
        compact = compact.upper()
        if re.fullmatch(r"W[O0]\d{2}", compact):
            compact = "W0" + compact[2:]
    elif name == "物料编号":
        compact = re.sub(r"^(?:SW|SI)-", "SM-", compact, flags=re.IGNORECASE)
        # Samsung's reviewed material-code alphabet uses digit zero rather
        # than letter O.  PP-OCR Server systematically emits O in dense rows
        # (for example TOS926/CNFC and DX92O/EGCN).  Limit correction to a
        # leading structured SKU with a known Samsung market suffix; free-form
        # descriptions and unknown product families are left untouched.
        code_match = re.match(r"([A-Za-z]{1,3}-[A-Za-z0-9-]{6,})(.*)", compact)
        if code_match:
            code, description = code_match.groups()
            upper_code = code.upper()
            if (
                "O" in upper_code
                and any(char.isdigit() for char in upper_code)
                and upper_code.endswith(("CHC", "EGCN", "GCN", "CNFC", "YC"))
            ):
                compact = upper_code.replace("O", "0") + description
    return compact


def _same_decimal(left: str, right: str) -> bool:
    try:
        return bool(left and right) and float(left.replace(" ", "")) == float(right.replace(" ", ""))
    except ValueError:
        return False


def _product_cell_confidence(name: str, value: str, rows: list[TextObservation]) -> float:
    if not value or not rows:
        return 0.0
    total_chars = sum(max(1, len(row.text.strip())) for row in rows)
    raw = sum(float(row.confidence) * max(1, len(row.text.strip())) for row in rows) / total_chars
    compact = re.sub(r"\s", "", value)
    validated = False
    if name in {"行号", "数量"}:
        validated = bool(re.fullmatch(r"\d+(?:\.\d+)?", compact))
    elif name in {"重量", "体积"}:
        validated = bool(re.fullmatch(r"\d+(?:\.\s*\d+)?", compact))
    elif name == "EAN码":
        validated = bool(re.fullmatch(r"\d{13}", compact)) and _valid_ean13(compact)
    elif name == "出库仓库":
        validated = bool(re.fullmatch(r"[A-Z]{1,3}\d{1,4}", compact.upper()))
    elif name == "等级":
        validated = bool(re.fullmatch(r"[A-Z][A-Z0-9]?", compact.upper()))
    elif name == "产品类别":
        validated = bool(re.fullmatch(r"[A-Z]\d", compact.upper()))
    elif name == "物料编号":
        validated = len(compact) >= 7 and bool(re.search(r"[A-Za-z0-9]", compact))
    # Type/校验位通过时提升可靠度，但仍保留 OCR 的不确定性。
    if validated:
        floors = {
            "EAN码": 0.99,
            "行号": 0.9,
            "数量": 0.9,
            "重量": 0.9,
            "体积": 0.9,
            "出库仓库": 0.88,
            "等级": 0.85,
            "产品类别": 0.86,
            # 只验证了编码形态，不能证明其中每个易混淆字符都正确。
            "物料编号": 0.70,
        }
        raw = max(raw, floors.get(name, 0.75))
    return round(max(0.0, min(1.0, raw)), 3)


def _valid_ean13(value: str) -> bool:
    digits = [int(char) for char in value]
    check = (10 - (sum(digits[:12:2]) + 3 * sum(digits[1:12:2])) % 10) % 10
    return check == digits[-1]


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


def parse_date(text: str) -> date | None:
    cleaned = text.replace("O", "0").replace("o", "0")
    match = DATE_PATTERN.search(cleaned)
    if not match:
        return None
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def parse_receipt_date(text: str, required: date | None) -> date | None:
    strict = parse_date(text)
    if strict and (required is None or strict.year == required.year):
        return strict
    if strict and required is not None and strict.year != required.year:
        return None
    if required is None:
        return None
    compact = re.sub(r"\s", "", text.replace("O", "0").replace("o", "0"))
    explicit_year = re.search(r"(?<!\d)(\d{4})年", compact)
    if explicit_year and int(explicit_year.group(1)) != required.year:
        # A visibly present but different/invalid four-digit year is contrary
        # evidence, not a missing year that may inherit the printed required
        # year. For example, ``1105年2月23`` must not become 2025-02-23.
        return None
    compact = re.sub(r"(?<=\d)[司曰目可「口T丁川]$", "日", compact, flags=re.IGNORECASE)
    # Handwritten dates at the right edge often lose the final “日”.  A full
    # year/month/day expression still provides independent evidence; short
    # fragments without both 年 and 月 remain rejected.
    if "日" not in compact and re.search(r"年[^0-9]{0,2}\d{1,2}月[^0-9]{0,2}\d{1,2}$", compact):
        compact += "日"
    if "日" not in compact:
        return None
    day_match = re.search(r"月[^0-9]{0,2}(\d{1,2})日", compact)
    if not day_match:
        return None
    day = int(day_match.group(1))
    month_match = re.search(r"(?:年|[-./])[^0-9]{0,2}(\d{1,2})月", compact)
    month = int(month_match.group(1)) if month_match else required.month
    try:
        return date(required.year, month, day)
    except ValueError:
        return None


def find_receipt_date(
    rows: list[TextObservation],
    required_text: str = "",
) -> tuple[date | None, TextObservation | None]:
    required = parse_date(required_text)
    candidates: dict[date, list[tuple[float, TextObservation]]] = {}
    for row in rows:
        found = parse_receipt_date(row.text, required)
        if not found:
            continue
        # The receiving date is on the right side of the signature table.
        if 0.50 <= row.y <= 0.69 and row.x >= 0.58:
            score = row.confidence + row.x + (0.2 if "年" in row.text else 0)
            candidates.setdefault(found, []).append((score, row))
    if not candidates:
        return None, None
    ranked: list[tuple[float, date, TextObservation]] = []
    for found, evidence in candidates.items():
        best_score, best_row = max(evidence, key=lambda item: item[0])
        # Independent OCR variants are stronger than one locally confident
        # misread. Matching the required date is a prior, never a fabricated
        # value: the bonus applies only when OCR actually produced that date.
        consensus_bonus = min(0.36, 0.12 * (len(evidence) - 1))
        required_bonus = 0.35 if required is not None and found == required else 0.0
        ranked.append((best_score + consensus_bonus + required_bonus, found, best_row))
    _, found, row = max(ranked, key=lambda item: item[0])
    return found, row


def compare_dates(required_text: str, actual: date | None) -> dict:
    required = parse_date(required_text)
    if required is None:
        status, message = "无法判断", "未识别到要求到货日期"
    elif actual is None:
        status, message = "未识别", "未识别到收货日期"
    elif required == actual:
        status, message = "匹配", "收货日期与要求到货日期一致"
    else:
        delta = (actual - required).days
        status = "不匹配"
        message = f"实际收货比要求日期{'晚' if delta > 0 else '早'} {abs(delta)} 天"
    return {
        "required": required.isoformat() if required else "",
        "actual": actual.isoformat() if actual else "",
        "status": status,
        "message": message,
    }


def estimate_date_confidence(
    rows: list[TextObservation],
    required_text: str,
    actual: date | None,
) -> float:
    if actual is None:
        return 0.0
    required = parse_date(required_text)
    evidence: list[tuple[float, bool]] = []
    for row in rows:
        parsed = parse_receipt_date(row.text, required)
        if parsed != actual:
            continue
        strict = parse_date(row.text) == actual
        evidence.append((float(row.confidence), strict))
    if not evidence:
        return 0.35
    score = max(confidence for confidence, _ in evidence)
    # A repaired candidate such as ``202年2月2日`` is ambiguous when the
    # printed requirement has a two-digit day: the OCR may have clipped 21,
    # 22 or 27 to a single 2.  Keep the displayed candidate, but never let
    # repeated partial readings turn it into an automatic pass/rejection.
    compact_rows = [
        re.sub(r"\s", "", row.text)
        for row in rows
        if parse_receipt_date(row.text, required) == actual
    ]
    ambiguous_single_day = bool(
        required is not None
        and required.day >= 10
        and actual.day < 10
        and not any(strict for _, strict in evidence)
        and compact_rows
        and all(not re.search(r"\d{4}[^\d]{0,2}\d{1,2}[^\d]{0,2}\d{2}", text) for text in compact_rows)
    )
    if ambiguous_single_day:
        return round(min(0.6, score), 3)
    # One month/day fragment that happens to equal the printed requirement is
    # not an independent receipt-date verdict.  Real handwriting can make 21
    # look like 25 (7284653895); the required date then acts as a dangerous
    # prior.  Keep the candidate visible, but require either a strict full
    # date or multiple accepted observations before automatic matching.
    if not any(strict for _, strict in evidence) and len(evidence) == 1:
        return round(min(0.68, score), 3)
    if any(strict for _, strict in evidence):
        score += 0.2
    if len(evidence) >= 2:
        score += 0.22
    elif evidence and not any(strict for _, strict in evidence):
        score += 0.08
    return round(min(1.0, score), 3)


def compare_seal_text(requirement: str, recognized_texts: list[str]) -> dict:
    expected = normalize_text(requirement)
    expected_company = _company_name(expected)
    expected_company_core = _company_core(expected_company)
    normalized_recognized = [
        normalize_text(text) for text in recognized_texts if normalize_text(text)
    ]
    company_conflict = _has_conflicting_complete_company(
        expected_company, normalized_recognized
    )
    company_marker_present = "有限公司" in expected_company
    company_only_requirement = bool(
        company_marker_present and expected == expected_company
    )
    # One or two OCR glyph errors are common on curved company names.  The
    # threshold still rejects the known different-company sample (0.625),
    # while retaining true names with one missing/substituted glyph.
    company_threshold = 0.70
    best_text, best_score = "", 0.0
    best_reliable_text, best_reliable_score = "", 0.0
    best_company_score = 0.0
    for text in recognized_texts:
        actual = normalize_text(text)
        if not actual:
            continue
        sequence = SequenceMatcher(None, expected, actual).ratio()
        company_sequence = _partial_similarity(expected_company, actual) if expected_company else 0.0
        expected_chars, actual_chars = set(expected), set(actual)
        coverage = len(expected_chars & actual_chars) / max(1, len(expected_chars))
        company_core_score = (
            _partial_similarity(expected_company_core, actual)
            if expected_company_core else 0.0
        )
        # Circular stamps may be unwrapped from the opposite side of the
        # ring, so a two-character place prefix can appear reversed (南昌 →
        # 昌南) while the distinctive organization core is read elsewhere in
        # the same region. Treat this as corroborated company evidence only
        # when the remaining core is long and independently matches well.
        circular_prefix_match = _circular_prefix_company_match(
            expected_company_core, actual
        )
        if circular_prefix_match:
            company_core_score = max(company_core_score, 0.88)
        # Exact containment is strong only when the recognized substring covers
        # most of the requirement.  A station number or short stamp suffix can
        # also be a literal substring, but must never receive a perfect score.
        contains_bonus = 0.0
        if expected in actual:
            contains_bonus = 1.0
        elif actual in expected and len(actual) >= max(8, int(len(expected) * 0.75)):
            contains_bonus = 1.0
        station_stamp_match = _matching_station_stamp(requirement, text)
        samsung_service_stamp_match = _matching_samsung_service_stamp(
            requirement, text
        )
        score = max(
            sequence, company_sequence * 0.96, coverage * 0.9,
            contains_bonus, 0.88 if circular_prefix_match else 0.0,
            0.94 if station_stamp_match else 0.0,
            0.90 if samsung_service_stamp_match else 0.0,
        )
        # A company-only requirement has no separate stamp-type suffix to
        # recover.  Accept the lower raw boundary only when the legal marker
        # (or its common one-glyph-clipped form) survives and the distinctive company core is independently
        # strong.  This keeps generic ``有限公司`` overlap from hiding another
        # organization while rescuing fragmented curved-company OCR.
        company_only_match = bool(
            company_only_requirement
            and ("有限公司" in actual or "限公司" in actual)
            and company_core_score >= 0.82
            and score >= 0.72
        )
        if score > best_score:
            best_text, best_score, best_company_score = text, score, company_core_score
        if (
            (
                score >= 0.78
                or station_stamp_match
                or samsung_service_stamp_match
                or company_only_match
            )
            and (not company_marker_present or company_core_score >= company_threshold)
            and _has_required_suffix(requirement, text)
            and not company_conflict
            and score > best_reliable_score
        ):
            best_reliable_text, best_reliable_score = text, score

    # A reviewed Samsung local-service round stamp places the brand, city and
    # ``服务中心`` around different arcs.  Color-only Server transforms can
    # expose them in one same-region aggregate without ever producing the
    # original circular order.  Accept only this exact template structure and
    # the observed 孝/考 OCR confusion; do not grant generic one-character
    # city fuzziness that could confuse two genuinely different service sites.
    fragmented_local_service_text = next(
        (
            text for text in recognized_texts
            if _matching_fragmented_local_samsung_service_stamp(
                requirement, text
            )
        ),
        "",
    )
    if fragmented_local_service_text and not company_conflict:
        best_reliable_text = fragmented_local_service_text
        best_reliable_score = max(best_reliable_score, 0.90)

    # A company-only fragment can have the highest raw similarity while a
    # slightly noisier same-region aggregate also contains the required stamp
    # type.  Prefer the latter as the displayed/verifying evidence because it
    # satisfies both parts of the business requirement.
    if best_reliable_text:
        best_text, best_score = best_reliable_text, best_reliable_score
        best_company_score = _partial_similarity(
            expected_company_core, normalize_text(best_reliable_text)
        )
        if _circular_prefix_company_match(
            expected_company_core, normalize_text(best_reliable_text)
        ):
            best_company_score = max(best_company_score, 0.88)

    if not requirement:
        status, message = "无法判断", "未识别到签章要求"
    elif not best_text:
        status, message = "未识别", "检测到的收货章中未识别出文字"
    elif best_score >= 0.72 and best_reliable_text:
        status, message = "匹配", "收货章内容与签章要求一致"
    elif best_score >= 0.72:
        status, message = (
            ("无法判断", "识别到完整但不同的公司全称，禁止模糊判定通过")
            if company_conflict else
            ("无法判断", "印章存在相似文字，但公司名或章类型证据不足")
        )
    else:
        status, message = "不匹配", "收货章内容与签章要求不一致"
    return {
        "requirement": requirement,
        "recognized": best_text,
        "all_recognized": recognized_texts,
        "score": round(best_score, 3),
        "status": status,
        "message": message,
        "confidence": round(best_score, 3),
        "company_score": round(best_company_score, 3),
        "company_conflict": company_conflict,
        "reliable": bool(best_reliable_text),
    }


def _matching_station_stamp(requirement: str, actual: str) -> bool:
    """Require both a specific pickup-stamp type and its exact station id."""
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    if "取机专用章" not in expected or "取机专用章" not in recognized:
        return False
    # Search before stripping parentheses: ``（2）6237143站`` must not turn
    # into the artificial combined number ``26237143站``.
    station = re.search(r"(\d{6,})\s*站", requirement)
    actual_compact = re.sub(r"\s", "", actual)
    return bool(station and f"{station.group(1)}站" in actual_compact)


def _matching_samsung_service_stamp(requirement: str, actual: str) -> bool:
    """Recognize the bilingual Samsung customer-service stamp safely.

    Some light red stamps expose the Latin brand and only the Chinese stamp
    type, while losing the curved ``三星电子客户服务中心`` text.  The three
    independent tokens below are specific to this reviewed stamp template;
    ``SAMSUNG`` alone or a generic service fragment is never sufficient.
    """
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    dedicated_service_stamp = bool(
        "三星电子" in expected
        and "客户服务中心" in expected
        and "服务专用章" in expected
        and "samsung" in recognized
        and "服务" in recognized
        and ("专用章" in recognized or "用章" in recognized)
    )
    # A second reviewed template prints the requirement itself as the
    # bilingual brand plus ``客户服务中心`` and has no ``专用章`` suffix.  Match
    # it only when the seal region independently exposes the Latin brand and
    # the complete Chinese ``服务中心`` type.  Either token alone remains too
    # generic for an automatic conclusion.
    bilingual_customer_center = bool(
        "三星" in expected
        and "samsung" in expected
        and "客户服务中心" in expected
        and "samsung" in recognized
        and "服务中心" in recognized
    )
    return dedicated_service_stamp or bilingual_customer_center


def _matching_fragmented_local_samsung_service_stamp(
    requirement: str, actual: str
) -> bool:
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    match = re.fullmatch(
        r"三星电子(?P<place>[\u4e00-\u9fff]{2,6})服务中心",
        expected,
    )
    if not match or len(recognized) > 80:
        return False
    place = match.group("place")
    place_seen = place in recognized or (
        place == "孝感" and "考感" in recognized
    )
    brand_seen = any(
        token in recognized for token in ("三星电子", "三星电", "星电子", "星电")
    )
    service_seen = "服务中" in recognized or (
        "服务" in recognized and "中心" in recognized
    )
    return bool(place_seen and brand_seen and service_seen)


def _has_required_suffix(requirement: str, actual: str) -> bool:
    expected = normalize_text(requirement)
    recognized = normalize_text(actual)
    # Station/code identifiers are business keys, not fuzzy text.  A stamp
    # with one missing or substituted digit must stay in review even when the
    # surrounding ``三星售后``/company wording makes the overall similarity
    # exceed the normal threshold.  Telephone numbers elsewhere in the
    # requirement are deliberately excluded from this exact-key check.
    station_ids = re.findall(r"(\d{6,12})\s*站", requirement)
    code_ids = re.findall(
        r"(?:授权代码|站代码|代码)\s*[:：]?\s*(\d{6,12})",
        requirement,
    )
    if any(identifier not in recognized for identifier in station_ids + code_ids):
        return False
    if "业务受理" in expected:
        branch = re.search(r"业务受理(\d+)", expected)
        return bool(
            "业务受理" in recognized
            and (not branch or f"业务受理{branch.group(1)}" in recognized)
        )
    marker = "有限公司"
    index = expected.find(marker)
    if index < 0:
        return len(recognized) >= max(6, int(len(expected) * 0.6))
    suffix = expected[index + len(marker) :]
    if not suffix:
        return len(recognized) >= int(len(expected) * 0.7)
    # A color-only Server crop can read the full legal company and the
    # distinctive center row ``业务专用`` while clipping only the terminal
    # generic glyph ``章``.  Accept this narrowly: both the exact complete
    # company and all four type glyphs must occur in the same region-level
    # aggregate. Company-only evidence or a generic ``专用`` fragment remains
    # insufficient.
    if (
        "业务专用章" in suffix
        and expected[: index + len(marker)] in recognized
        and "业务专用" in recognized
    ):
        return True
    # If the required stamp type is explicit, at least one meaningful suffix token must survive OCR.
    tokens = [token for token in ("收货章", "专用章", "服务", "售后", "仓储部", "维修中心") if token in suffix]
    return not tokens or any(token in recognized for token in tokens)


def _company_name(text: str) -> str:
    marker = "有限公司"
    index = text.find(marker)
    return text[: index + len(marker)] if index >= 0 else text


def _company_core(text: str) -> str:
    """Remove generic legal suffixes before checking company-name evidence."""
    core = text
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    return core or text


def _has_conflicting_complete_company(
    expected_company: str, recognized_texts: list[str]
) -> bool:
    """Veto fuzzy matching when OCR exposes a complete near-name company.

    A real stamp from ``大连允华…有限公司`` differs from the required
    ``大连北华…有限公司`` by one distinctive glyph and can otherwise exceed a
    permissive curved-text similarity threshold.  If any transform also sees
    the exact expected company core, the alternate spelling is treated as an
    OCR error.  Without that corroboration, a complete similarly sized legal
    name is contradictory evidence and must stay in human review.
    """
    if not expected_company or "有限公司" not in expected_company:
        return False
    expected_core = _company_core(expected_company)
    exact_core_seen = bool(
        expected_core and any(expected_core in text for text in recognized_texts)
    )
    marker = "有限公司"
    expected_length = len(expected_company)
    for text in recognized_texts:
        # Aggregated same-region evidence intentionally concatenates several
        # transforms.  Sliding a company-length window across that long audit
        # string can manufacture a fake "alternate company" at a transform
        # boundary.  Conflict evidence must be an independently recognized
        # near-complete legal name, allowing only a short OCR prefix.
        cursor = 0
        while True:
            marker_index = text.find(marker, cursor)
            if marker_index < 0:
                break
            end = marker_index + len(marker)
            cursor = end
            # OCR can replace, add, or omit several glyphs in the distinctive
            # part of a legal company name.  Inspect a narrow length band
            # around the requirement instead of only an equal-length window;
            # otherwise ``十堰盛瑞…有限公司`` can incorrectly corroborate the
            # longer, different ``十堰市万盛达…有限公司`` requirement.
            minimum_length = max(len(marker) + 4, expected_length - 4)
            for candidate_length in range(minimum_length, expected_length + 5):
                if end < candidate_length:
                    continue
                start = end - candidate_length
                candidate = text[start:end]
                # An independent OCR row can legitimately contain the complete
                # legal company followed by a stamp type/branch number, e.g.
                # ``洛阳西利通信有限公司业务受理（2）``.  Judge the short
                # prefix/tail around the legal name.  Long transform aggregates
                # remain excluded because at least one side exceeds these
                # bounds or contains another legal-company marker.
                prefix = text[:start]
                suffix = text[end:]
                if (
                    len(prefix) > 4
                    or len(suffix) > 20
                    or marker in prefix
                    or marker in suffix
                ):
                    continue
                matcher = SequenceMatcher(None, expected_company, candidate)
                changed_glyphs = sum(
                    (expected_end - expected_start)
                    + (candidate_end - candidate_start)
                    for operation,
                    expected_start,
                    expected_end,
                    candidate_start,
                    candidate_end in matcher.get_opcodes()
                    if operation != "equal"
                )
                if (
                    candidate != expected_company
                    and matcher.ratio() >= 0.82
                    # A shorter observation with only one compact OCR wound is
                    # the established one-character tolerance case.  Require
                    # broader disagreement before treating unequal-length
                    # names as two different complete companies.  Equal-length
                    # near names keep the original stricter veto.
                    and (
                        candidate_length == expected_length
                        or changed_glyphs >= 4
                    )
                ):
                    # A partial or complete observation containing the expected
                    # distinctive company core can explain one alternate complete
                    # reading as an OCR spelling error.  Cross-model disagreement
                    # is preserved earlier in the analyzer: a later Server result
                    # is audit-only when regular evidence already raised this
                    # conflict, so it cannot manufacture this corroboration.
                    if exact_core_seen:
                        continue
                    return True
    return False


def _circular_prefix_company_match(expected_core: str, actual: str) -> bool:
    if len(expected_core) < 6:
        return False
    place_prefix = expected_core[:2]
    organization_core = expected_core[2:]
    return bool(
        place_prefix[::-1] in actual
        and _partial_similarity(organization_core, actual) >= 0.82
    )


def _partial_similarity(expected: str, actual: str) -> float:
    if not expected or not actual:
        return 0.0
    if len(actual) < max(6, int(len(expected) * 0.45)):
        return SequenceMatcher(None, expected, actual).ratio()
    if len(actual) < len(expected):
        return SequenceMatcher(None, expected, actual).ratio()
    if len(actual) - len(expected) > 120:
        return 0.0
    window = len(expected)
    candidates = [actual]
    if len(actual) > window:
        candidates.extend(actual[i : i + window] for i in range(len(actual) - window + 1))
    return max(SequenceMatcher(None, expected, candidate).ratio() for candidate in candidates)
