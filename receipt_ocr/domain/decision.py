"""Shared final verdict policy for machine and reviewed evidence."""

from .issues import issue, active_issues

from .fields.schema import derive_signature_check


def has_provider_failure(result: dict) -> bool:
    """Provider identity and failure codes decide failure, never UI text."""
    for variants in (result.get("recognition_variants") or {}).values():
        for variant in variants or []:
            if variant.get("method") == "danzhengtong" and variant.get("error"):
                return True
    return any(item.get("code") == "provider_failure" and item.get("provider") == "danzhengtong"
               for item in result.get("review_issues", []))


def apply_signature_match_mode(result: dict) -> None:
    """Aggregate signature comparisons from every selected recognition result."""
    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
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


def decide_overall(
    date_check: dict, seal_check: dict, review_reasons: list[str], acceptance: dict | None = None,
    signature_check: dict | None = None, *, review_issues=None,
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
    if not seal_enabled:
        seal_check = {"status": "未核对", "reliable": True}
    issues = review_issues if review_issues is not None else [
        issue("review_required", "document", message) for message in review_reasons
    ]
    review_reasons = active_issues(issues, acceptance)
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
    issues = list(result.get("review_issues", [
        issue("review_required", "document", message)
        for message in result.get("review_reasons", [])
    ]))
    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
    if acceptance.get("low_confidence_mode", "check") == "check" and any(
        meta.get("low_confidence")
        for name, meta in result.get("field_metadata", {}).items()
        if name != "仓库接收人"
    ):
        issues.append(issue("low_confidence", "fields", "存在低置信度字段"))
    issues = active_issues(issues, acceptance)
    result["review_issues"] = issues
    reasons = list(dict.fromkeys(item["message"] for item in issues))
    result["review_reasons"] = reasons
    apply_signature_match_mode(result)
    provider_failed = has_provider_failure(result)
    overall = (
        "识别失败" if provider_failed else
        decide_overall(result.get("date_check", {}), result.get("seal_check", {}), reasons,
                       (result.get("recognition_config") or {}).get("acceptance"),
                       result.get("signature_check"), review_issues=issues)
    )
    result.update(
        overall=overall,
        final_result=overall,
        review_status="待复核" if overall == "需人工复核" else "无需复核",
    )
    return result
