"""Organization, address and business-code repairs backed by repeated fields."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .parsing_constants import (
    VERIFIED_CUSTOMER_BY_SHIP_TO_CODE,
    VERIFIED_CUSTOMER_BY_STATION,
    VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE,
)
from .parsing_text import normalize_text


def _clean_organization_boundaries(
    fields: dict[str, str],
    corrections: dict[str, dict],
) -> None:
    """Remove only observed border artifacts next to organization fields."""
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


def _repair_shipping_unit(
    fields: dict[str, str],
    corrections: dict[str, dict],
) -> None:
    """Repair the one reviewed shipping-unit leading-character dropout."""
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


def _repair_receipt_address(
    fields: dict[str, str],
    corrections: dict[str, dict],
) -> None:
    """Use the repeated address only when its number groups agree."""
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


def _repair_destination_business_codes(
    fields: dict[str, str],
    corrections: dict[str, dict],
) -> tuple[str, str, str]:
    """Repair destination codes and unusable names from same-page evidence."""
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
    return customer, warehouse, requirement


def _repair_verified_customer_identity(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
    requirement: str,
) -> tuple[str, str]:
    """Apply reviewed identity masters before and after cross-field repair."""
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
    return customer, warehouse
