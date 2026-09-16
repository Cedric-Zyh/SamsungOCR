"""Printed stamp-requirement repairs; these rules do not read the actual seal."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .parsing_constants import (
    VERIFIED_PICKUP_REQUIREMENT_BY_STATION,
    VERIFIED_REQUIREMENT_BY_CUSTOMER,
)
from .parsing_text import normalize_text


def _repair_repeated_company_requirement(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
) -> tuple[str, str]:
    """Use exact repeated companies to recover names and oval-code wording."""
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
    return customer, requirement


def _repair_service_center_requirement(
    fields: dict[str, str],
    corrections: dict[str, dict],
    requirement: str,
) -> str:
    """Repair reviewed missing service-center glyphs with structural evidence."""
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
    return requirement


def _clean_requirement_footer_overlap(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
    requirement: str,
) -> str:
    """Remove reviewed receipt-note and quantity fragments from the requirement."""
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
    return requirement


def _repair_requirement_company_prefix(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
    requirement: str,
) -> None:
    """Recover supported company prefixes, then recheck exposed suffixes."""
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


def _repair_requirement_stamp_type(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
) -> None:
    """Apply ordered pickup, stamp-type and repeated-company cleanup rules."""
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


def _repair_customer_requirement_master(
    fields: dict[str, str],
    corrections: dict[str, dict],
    customer: str,
    warehouse: str,
) -> None:
    """Apply a reviewed stamp master only with repeated organization evidence."""
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
