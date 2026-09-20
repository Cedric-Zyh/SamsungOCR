"""Shared final verdict policy for machine and reviewed evidence."""

import re

from .field_schema import derive_signature_check


OPTIONAL_STAGE_REASONS = {
    "部分识别：未执行项目不能据此判定整单通过",
    "本次为部分识别，未执行项目不参与整单通过判定",
    # Signature/name comparison is shown for audit and may be confirmed by a
    # reviewer, but it is not part of the overall acceptance gate.
    "签收填写内容需人工确认",
}


_PROVIDER_NAME = re.compile(r"单证通|danzhengtong", re.IGNORECASE)
_PROVIDER_FAILURE = re.compile(
    r"失败|超时|timeout|connectionerror|readtimeout|max retries|nodename|无法连接|连接失败",
    re.IGNORECASE,
)


def has_provider_failure(result: dict) -> bool:
    """Recognize provider failures even when the outer OCR job completed."""
    for variants in (result.get("recognition_variants") or {}).values():
        for variant in variants or []:
            if variant.get("method") == "danzhengtong" and variant.get("error"):
                return True
    messages = [result.get("error_message"), *(result.get("review_reasons") or [])]
    return any(
        _PROVIDER_NAME.search(str(message)) and _PROVIDER_FAILURE.search(str(message))
        for message in messages if message
    )


def apply_signature_match_mode(result: dict) -> None:
    """Aggregate signature comparisons from every selected recognition result."""
    acceptance = result.get("recognition_config", {}).get("acceptance") or {}
    mode = acceptance.get("signature_match_mode")
    if mode not in {"any", "all", "none"}:
        return
    if mode == "none":
        result["signature_check"] = {
            **result.get("signature_check", {}), "status": "未核对", "reliable": True,
            "source": "按设置跳过", "match_mode": mode,
        }
        return
    base = {**(result.get("internal_fields") or {}), **(result.get("fields") or {})}
    checks = []
    for stage in ("fields", "handwriting"):
        for variant in (result.get("recognition_variants") or {}).get(stage, []):
            details = variant.get("details") or {}
            fields = {**base, **(details.get("fields") or {}), **(details.get("handwriting_fields") or {})}
            if fields.get("仓库联系人") or fields.get("仓库接收人"):
                checks.append(derive_signature_check(fields))
    if not checks:
        return
    matches = [check.get("status") == "匹配" and check.get("reliable") is True for check in checks]
    matched = all(matches) if mode == "all" else any(matches)
    all_mismatch = all(check.get("status") == "不匹配" and check.get("reliable") is True for check in checks)
    result["signature_check"] = {
        **result.get("signature_check", {}),
        "status": "匹配" if matched else "不匹配" if all_mismatch else "需人工复核",
        "reliable": matched or all_mismatch,
        "source": "多种识别结果",
        "match_mode": mode,
    }


def blocking_review_reasons(reasons):
    """Only date and seal evidence decide the machine pass verdict for now.

    Product and handwriting rules are intentionally not part of the current
    acceptance policy. Keep filtering these legacy notices here so records
    created before the policy change are also eligible when date and seal are
    both reliable matches.
    """
    return [reason for reason in reasons if str(reason).strip() not in OPTIONAL_STAGE_REASONS]


def decide_overall(
    date_check: dict, seal_check: dict, review_reasons: list[str], acceptance: dict | None = None,
    signature_check: dict | None = None,
) -> str:
    """Never auto-pass or auto-fail when date/seal evidence itself is unreliable."""
    acceptance = acceptance or {}
    low_confidence_mode = acceptance.get("low_confidence_mode", "check")
    date_enabled = acceptance.get("date_match_mode") != "none"
    seal_enabled = acceptance.get("seal_match_mode") != "none"
    signature_enabled = acceptance.get("signature_match_mode") in {"any", "all"}
    signature_check = signature_check or {}
    reject_mode = acceptance.get("reject_mode", "none")
    if not date_enabled:
        date_check = {"status": "未核对", "reliable": True}
        review_reasons = [reason for reason in review_reasons if not re.search(r"日期|到货", str(reason))]
    if not seal_enabled:
        seal_check = {"status": "未核对", "reliable": True}
        review_reasons = [reason for reason in review_reasons if not re.search(r"印章|签章", str(reason))]
    review_reasons = blocking_review_reasons(review_reasons)
    if low_confidence_mode == "ignore":
        review_reasons = [reason for reason in review_reasons if "低置信度" not in str(reason)]
    if (
        review_reasons
        or not date_check.get("reliable")
        or not seal_check.get("reliable")
        or (signature_enabled and not signature_check.get("reliable"))
    ):
        return "需人工复核"
    # A machine-read date or seal mismatch is evidence for the reviewer, not a
    # final rejection.  The reviewer must inspect the receipt and explicitly
    # confirm the mismatch before the record can become "不通过".
    if ((date_enabled and date_check.get("status") == "不匹配")
            or (seal_enabled and seal_check.get("status") == "不匹配")
            or (signature_enabled and signature_check.get("status") == "不匹配")):
        mismatches = [check.get("status") == "不匹配" for enabled, check in (
            (date_enabled, date_check), (seal_enabled, seal_check), (signature_enabled, signature_check)
        ) if enabled]
        if (reject_mode == "any_mismatch" and any(mismatches)) or (
                reject_mode == "all_mismatch" and mismatches and all(mismatches)):
            return "不通过"
        return "需人工复核"
    if ((not date_enabled or date_check.get("status") == "匹配")
            and (not seal_enabled or seal_check.get("status") == "匹配")
            and (not signature_enabled or signature_check.get("status") == "匹配")):
        return "通过"
    return "需人工复核"


def finalize_result(result: dict) -> dict:
    """Write every machine verdict field from the completed stage evidence."""
    reasons = blocking_review_reasons(list(result.get("review_reasons", [])))
    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
    if acceptance.get("low_confidence_mode", "check") == "check" and any(
        meta.get("low_confidence")
        for name, meta in result.get("field_metadata", {}).items()
        if name != "仓库接收人"
    ):
        reasons.append("存在低置信度字段")
    reasons = list(dict.fromkeys(reasons))
    result["review_reasons"] = reasons
    apply_signature_match_mode(result)
    provider_failed = has_provider_failure(result)
    overall = (
        "识别失败" if provider_failed else
        decide_overall(result.get("date_check", {}), result.get("seal_check", {}), reasons,
                       (result.get("recognition_config") or {}).get("acceptance"),
                       result.get("signature_check"))
    )
    result.update(
        overall=overall,
        final_result=overall,
        review_status="待复核" if overall == "需人工复核" else "无需复核",
    )
    return result
