"""Reapply provider comparison rules to saved machine evidence without OCR calls."""

from copy import deepcopy

from .domain.decision import apply_signature_match_mode, finalize_result
from .domain.handwriting_selection import select_handwriting
from .domain.printed_field_selection import canonical_printed_field, select_printed_fields
from .domain.issues import issue
from .persistence.issue_migration import OPTIONAL_STAGE_REASONS, migrate_issues
from .domain.fields.schema import PRINTED_FIELDS, derive_signature_check
from .domain.parsing import compare_dates, parse_date
from .domain.parsing.parsing_seals import compare_seal_text_strict
from .provider_field_policy import DZT_LAYOUT_REASONS, accept_real_dzt_fields
from receipt_ocr.recognition.seal.providers.qingtong import compare_qingtong_seal
from receipt_ocr.recognition.seal.postprocess.providers import combine_seal_provider_checks


REFRESH_ACTION = "识别规则更新"
_OLD_DZT_REASON = "单证通日期/印章为文字提取结果，需人工核对原图"
_HANDWRITING_CONFLICT = "手写签名：多种识别方式结果不一致"
_PRINTED_CONFLICT = "印刷字段：多种识别方式结果不一致"
_RESOLVED_REASONS = {
    "date": {"收货日期无法可靠判断", "签收日期尚未可靠识别",
             "收货日期尚未可靠识别或人工确认"},
    "seal": {"印章内容无法可靠判断", "客户印章尚未可靠识别",
             "印章内容尚未可靠识别或人工确认", "客户印章：多种识别方式结果不一致"},
}


def has_human_edits(result):
    """Skip the whole record when any saved human decision could be affected."""
    if (result.get("review_status") in {"确认通过", "确认不通过"}
            or result.get("human_note") or result.get("page_review_override")):
        return True
    checks = [result.get(key) or {} for key in ("date_check", "seal_check", "signature_check")]
    metadata = list((result.get("field_metadata") or {}).values())
    for evidence in [*checks, *metadata]:
        if ("人工" in str(evidence.get("source", ""))
                or "人工" in str(evidence.get("backend", ""))
                or evidence.get("human_confirmed")
                or "human_confirmed_match" in evidence):
            return True
    rows = (result.get("product_table") or {}).get("rows") or []
    return any("人工" in str(value) for row in rows for value in (row.get("sources") or {}).values())


def _live_trace(trace):
    return (isinstance(trace, dict) and trace.get("simulated") is False
            and trace.get("mode") == "real" and trace.get("status") == "completed")


def _refresh_check(check, stage, method, fields, trace):
    if not check or check.get("status") in {"未执行", "识别失败"}:
        return check
    if stage == "seal" and method == "qingtong":
        external = check.get("api") or {}
        if external.get("ok") is not True or not isinstance(external.get("response"), dict):
            return check
        rebuilt = compare_qingtong_seal(check.get("requirement") or fields.get("签章要求", ""), external)
        return {**check, **rebuilt}
    if method != "danzhengtong" or not _live_trace(trace) or check.get("simulated") is True:
        return check
    commit = (trace.get("result") or {}).get("commitResult") or {}
    if stage == "date":
        item = commit.get("签收日期") or {}
        text = item.get("value") if isinstance(item, dict) else None
        text = check.get("raw_text", check.get("actual", "")) if text is None else str(text)
        rebuilt = compare_dates(canonical_printed_field("要求到货", check.get("required") or fields.get("要求到货", "")),
                                parse_date(canonical_printed_field("要求到货", text)))
        rebuilt.update(reliable=rebuilt["status"] in {"匹配", "不匹配"}, raw_text=text)
    else:
        item = commit.get("收货客户印章") or {}
        text = item.get("value") if isinstance(item, dict) else None
        text = check.get("recognized", "") if text is None else str(text)
        rebuilt = compare_seal_text_strict(check.get("requirement") or fields.get("签章要求", ""),
                                           [text] if text else [])
        rebuilt["recognition_mode"] = "danzhengtong"
    rebuilt.update(simulated=False, backend="单证通", source="单证通")
    return {**check, **rebuilt}


def _method(check):
    if check.get("dual_check") or check.get("recognition_mode") == "qingtong_only":
        return "qingtong"
    if check.get("backend") == "单证通" or check.get("recognition_mode") == "danzhengtong":
        return "danzhengtong"
    return ""


def _refresh_document_fields(result):
    """Accept saved real field evidence without inferring a visual document class."""
    variants = (result.get("recognition_variants") or {}).get("fields") or []
    candidates = []
    for variant in variants:
        if variant.get("method") != "danzhengtong" or variant.get("error"):
            continue
        details = variant.get("details") or {}
        metadata = details.get("field_metadata") or {}
        if any(meta.get("simulated") is True for meta in metadata.values()):
            continue
        candidates.append((details.get("fields") or {},
                           details.get("danzhengtong") or result.get("danzhengtong") or {}))
    if (not variants and "danzhengtong" in ((result.get("recognition_config") or {}).get("fields") or [])
            and (result.get("recognition_status") or {}).get("fields") not in {"未执行", "识别失败"}):
        # Older saves may lack variants. Require both the selected field stage
        # and provider provenance so a date/seal-only trace cannot waive layout review.
        metadata = result.get("field_metadata") or {}
        provider_fields = {name: value for name, value in (result.get("fields") or {}).items()
                           if name in PRINTED_FIELDS and metadata.get(name, {}).get("source") == "单证通"
                           and metadata.get(name, {}).get("simulated") is not True}
        candidates.append((provider_fields, result.get("danzhengtong") or {}))
    accepted = False
    resolved = set()
    document = result.get("document_type") or {}
    for fields, trace in candidates:
        if not _live_trace(trace):
            continue
        updated = accept_real_dzt_fields(document, fields=fields, trace=trace)
        if updated.get("provider_fields_accepted"):
            accepted = True
            resolved.update((set(document.get("reasons", [])) - set(updated.get("reasons", []))) & DZT_LAYOUT_REASONS)
            document = updated
    if accepted:
        result["document_type"] = document
    return accepted, resolved


def _handwriting_conflict(item):
    return (item.get("message") == _HANDWRITING_CONFLICT
            or (item.get("code") == "provider_conflict" and item.get("scope") == "handwriting"))


def refresh_handwriting_result(current):
    """Repair only saved receiver selection, leaving provider evidence intact.

    This is an explicit repair operation, not a database read projection.
    Callers that persist it must also check review history under their write
    lock, as the general saved-rule repair command does.
    """
    result = deepcopy(current)
    variants = (result.get("recognition_variants") or {}).get("handwriting") or []
    if has_human_edits(current) or not variants:
        return result
    selection = select_handwriting(variants)
    result["handwriting_selection"] = {key: deepcopy(selection[key])
                                       for key in ("selected_method", "conflict", "reason")}
    # An older record can contain a previously recognized receiver but no
    # surviving usable provider candidate. A repair must not erase that name.
    if selection["selected_method"] is not None:
        result.setdefault("fields", {}).update(selection["fields"])
        metadata = result.setdefault("field_metadata", {})
        for name in selection["fields"]:
            metadata.pop(name, None)
        metadata.update(selection["metadata"])
    previous_issues = list(result.get("review_issues", migrate_issues(result)))
    issues = [item for item in previous_issues if not _handwriting_conflict(item)]
    if selection["conflict"]:
        issues.append(issue("provider_conflict", "handwriting", _HANDWRITING_CONFLICT))
    result["review_issues"] = issues
    previous_reasons = result.get("review_reasons", [])
    reasons = [reason for reason in previous_reasons if reason != _HANDWRITING_CONFLICT]
    if selection["conflict"]:
        reasons.append(_HANDWRITING_CONFLICT)
    result["review_reasons"] = list(dict.fromkeys(reasons))
    if not selection["conflict"] and "stage_review_reasons" in result:
        result["stage_review_reasons"] = [reason for reason in result["stage_review_reasons"]
                                          if reason != _HANDWRITING_CONFLICT]
    previous_signature = result.get("signature_check") or {}
    result["signature_check"] = derive_signature_check(result.get("fields") or {}, previous_signature)
    apply_signature_match_mode(result)
    signature_enabled = ((result.get("recognition_config") or {}).get("acceptance") or {}).get(
        "signature_match_mode") in {"any", "all"}
    if (issues != previous_issues or result["review_reasons"] != previous_reasons
            or (signature_enabled and result["signature_check"] != previous_signature)):
        return finalize_result(result)
    return result


def _printed_conflict(item):
    return (item.get("message") == _PRINTED_CONFLICT
            or (item.get("code") == "provider_conflict" and item.get("scope") == "fields"))


def _recompare_printed_requirement(check, stage, method, fields, trace, acceptance):
    """Recompare saved observations without treating an old verdict as evidence."""
    copied = deepcopy(check)
    requirement = fields.get("要求到货" if stage == "date" else "签章要求", "")
    if stage == "date":
        requirement = canonical_printed_field("要求到货", requirement)
    copied["required" if stage == "date" else "requirement"] = requirement
    if not check or check.get("status") in {"未执行", "识别失败"} or check.get("execution_status") == "skipped":
        return copied
    live_dzt = method == "danzhengtong" and _live_trace(trace) and check.get("simulated") is not True
    if live_dzt:
        commit = (trace.get("result") or {}).get("commitResult") or {}
        item = commit.get("签收日期" if stage == "date" else "收货客户印章") or {}
        value = item.get("value") if isinstance(item, dict) else None
        if value is not None:
            text = str(value)
            if stage == "date":
                rebuilt = compare_dates(requirement, parse_date(canonical_printed_field("要求到货", text)))
                rebuilt.update(reliable=rebuilt["status"] in {"匹配", "不匹配"}, raw_text=text)
            else:
                rebuilt = compare_seal_text_strict(requirement, [text] if text else [])
            return {**copied, **rebuilt}
    external = check.get("api") or {}
    if (stage == "seal" and method == "qingtong" and external.get("ok") is True
            and isinstance(external.get("response"), dict) and not check.get("simulated")):
        return {**copied, **compare_qingtong_seal(requirement, external,
                         match_mode=acceptance.get("seal_match_mode", "any"))}
    if (stage == "seal" and check.get("comparison_policy") in {"strict_text", "strict_information_fields"}
            and check.get("recognized") and not check.get("simulated")
            and method not in {"danzhengtong", "qingtong"}):
        rebuilt = compare_seal_text_strict(requirement, [check["recognized"]])
        rebuilt["reliable"] = bool(rebuilt.get("reliable") and check.get("reliable") is True)
        return {**copied, **rebuilt}
    # Historical local dates may have inherited digits from the old required
    # date.  Without independent saved OCR evidence they cannot prove a match
    # against the newly selected business requirement.
    copied.update(status="需人工复核", reliable=False,
                  message="比对依据已更新，需重新核对" + ("签收日期" if stage == "date" else "客户印章"))
    return copied


def _refresh_printed_dependency(result, stage):
    from .application.assembly import apply_match_mode, select_variants

    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
    fields = result.get("fields") or {}
    key = f"{stage}_check"
    saved = (result.get("recognition_variants") or {}).get(stage) or []
    variants = []
    for variant in saved:
        details = variant.get("details") or {}
        check = details.get(key) or {}
        if variant.get("error"):
            variants.append({"method": variant.get("method", ""), "error": variant["error"]})
        elif check:
            method = variant.get("method", "")
            rebuilt = _recompare_printed_requirement(check, stage, method, fields,
                details.get("danzhengtong") or result.get("danzhengtong") or {}, acceptance)
            variants.append({"method": method, "result": {key: rebuilt}})
    successful = [variant for variant in variants if "result" in variant]
    if successful:
        combined, decision_variants, primary = select_variants(stage, successful, acceptance)
        result[key] = deepcopy(combined if combined is not None else primary[key])
        mode = acceptance.get(f"{stage}_match_mode")
        if combined is None and mode not in {"any", "none"}:
            actual = "actual" if stage == "date" else "recognized"
            values = {(item["result"][key].get(actual), item["result"][key].get("status"))
                      for item in decision_variants}
            if len(values) > 1 or any(item["result"][key].get("reliable") is not True for item in decision_variants):
                result[key].update(status="需人工复核", reliable=False)
        apply_match_mode(result, stage, variants, successful, combined, acceptance)
    else:
        old = result.get(key) or {}
        result[key] = _recompare_printed_requirement(old, stage, _method(old), fields,
                                                    result.get("danzhengtong") or {}, acceptance)
        apply_match_mode(result, stage, variants, successful, None, acceptance)


def refresh_printed_fields_result(current):
    """Select saved printed fields independently; preserve raw provider evidence."""
    result = deepcopy(current)
    variants = (result.get("recognition_variants") or {}).get("fields") or []
    if has_human_edits(current) or not variants:
        return result
    selection = select_printed_fields(variants)
    selected = [name for name, item in selection["selections"].items() if item["selected_method"] is not None]
    old_issues = list(result.get("review_issues", migrate_issues(result)))
    old_reasons = result.get("review_reasons", [])
    if not selected and not any(_printed_conflict(item) for item in old_issues) and _PRINTED_CONFLICT not in old_reasons:
        return result
    result["printed_field_selection"] = {"fields": deepcopy(selection["selections"]),
                                         "conflicts": list(selection["conflicts"])}
    old_fields = deepcopy(result.get("fields") or {})
    fields = result.setdefault("fields", {})
    metadata = result.setdefault("field_metadata", {})
    fallbacks = result.get("field_fallbacks") or {}
    for name in selected:
        fields[name] = selection["fields"][name]
        chosen = deepcopy(selection["metadata"].get(name) or {})
        existing = metadata.get(name) or {}
        # Do not add synthetic defaults when the saved selected metadata already
        # carries the exact same provenance and value.
        if not (fields[name] == old_fields.get(name) and existing
                and {key: value for key, value in existing.items() if key not in {"value", "original"}}
                == {key: value for key, value in chosen.items() if key not in {"value", "original"}}):
            metadata[name] = chosen
        if name in selection["field_fallbacks"]:
            fallbacks[name] = deepcopy(selection["field_fallbacks"][name])
        elif (canonical_printed_field(name, fields[name]) != canonical_printed_field(name, old_fields.get(name))
              or chosen.get("source") != existing.get("source")):
            # Older variants omitted fallbacks. Keep an existing fallback
            # belonging to the same value/source instead of treating omission
            # as proof that the saved evidence no longer exists.
            fallbacks.pop(name, None)
    if fallbacks or "field_fallbacks" in result:
        result["field_fallbacks"] = fallbacks
    issues = [item for item in old_issues if not _printed_conflict(item)]
    if selection["conflicts"]:
        issues.append(issue("provider_conflict", "fields", _PRINTED_CONFLICT))
    # Only remove the shared low-confidence warning when every relevant current
    # field is present and no surviving field metadata carries that warning.
    if (all(fields.get(name) for name in ("客户名称", "要求到货", "签章要求"))
            and not any(meta.get("low_confidence") for name, meta in metadata.items() if name != "仓库接收人")):
        issues = [item for item in issues if not (item.get("code") == "low_confidence" and item.get("scope") == "fields")]
    result["review_issues"] = issues
    result["review_reasons"] = [reason for reason in old_reasons if reason != _PRINTED_CONFLICT]
    if selection["conflicts"]:
        result["review_reasons"].append(_PRINTED_CONFLICT)
    if "stage_review_reasons" in result and not selection["conflicts"]:
        result["stage_review_reasons"] = [reason for reason in result["stage_review_reasons"] if reason != _PRINTED_CONFLICT]
    for name, stage in (("要求到货", "date"), ("签章要求", "seal")):
        check = result.get(f"{stage}_check") or {}
        required_key = "required" if stage == "date" else "requirement"
        changed = canonical_printed_field(name, fields.get(name)) != canonical_printed_field(name, old_fields.get(name))
        stale = (name in selected and required_key in check
                 and canonical_printed_field(name, check[required_key]) != canonical_printed_field(name, fields.get(name)))
        if changed or stale:
            _refresh_printed_dependency(result, stage)
    effective_keys = ("fields", "field_metadata", "field_fallbacks", "review_reasons",
                      "stage_review_reasons", "date_check", "seal_check")
    if (not selection["conflicts"] and issues == old_issues
            and all(result.get(key) == current.get(key) for key in effective_keys)):
        # Selection diagnostics alone do not justify rewriting an unchanged
        # historical record or deriving unrelated review state.
        return deepcopy(current)
    result["signature_check"] = derive_signature_check(fields, result.get("signature_check"))
    apply_signature_match_mode(result)
    return finalize_result(result)


def refresh_provider_result(current):
    """Return a copy with refreshed provider decisions; preserve all human records."""
    if has_human_edits(current):
        return deepcopy(current)
    result = refresh_handwriting_result(current)
    fields = {**result.get("internal_fields", {}), **result.get("fields", {})}
    trace = result.get("danzhengtong") or {}
    accepted_fields, resolved_field_reasons = _refresh_document_fields(result)
    refreshed = set()
    live_dzt = False
    for stage in ("date", "seal"):
        key = f"{stage}_check"
        variants = (result.get("recognition_variants") or {}).get(stage) or []
        successful = []
        for variant in variants:
            details = variant.get("details") or {}
            old = details.get(key) or {}
            if variant.get("error") or not old or old.get("status") == "未执行":
                continue
            method = variant.get("method", "")
            provider_trace = details.get("danzhengtong") or trace
            new = _refresh_check(old, stage, method, fields, provider_trace)
            if new != old:
                refreshed.add(stage)
                details[key] = new
                variant["value"] = [new.get("actual" if stage == "date" else "recognized", ""), new.get("status")]
            live_dzt |= method == "danzhengtong" and _live_trace(provider_trace)
            successful.append(details[key])
        old = result.get(key) or {}
        combined = combine_seal_provider_checks(successful) if stage == "seal" else None
        if combined is not None:
            # A saved aggregate may still use an earlier policy even when its
            # individual provider comparisons already use the current rules.
            if combined != old:
                refreshed.add(stage)
                result[key] = combined
        elif successful and stage in refreshed:
            decision_checks = successful
            primary = deepcopy(decision_checks[0])
            value_key = "actual" if stage == "date" else "recognized"
            values = {(check.get(value_key, ""), check.get("status")) for check in decision_checks}
            if len(values) > 1:
                primary.update(status="需人工复核", reliable=False)
            elif any(not check.get("reliable") for check in decision_checks):
                primary["reliable"] = False
            result[key] = primary
        elif not successful:
            method = _method(old)
            new = _refresh_check(old, stage, method, fields, trace)
            if stage == "seal":
                new = combine_seal_provider_checks([new]) or new
            if new != old:
                refreshed.add(stage)
                result[key] = new
            live_dzt |= method == "danzhengtong" and _live_trace(trace)
    # Even when no provider field is re-read, old records may still carry the
    # legacy "partial recognition" notice.  That notice is informational now;
    # remove it so saved results are recalculated with the current policy.
    remove = set(OPTIONAL_STAGE_REASONS)
    if live_dzt:
        remove.add(_OLD_DZT_REASON)
    if accepted_fields:
        remove.update(resolved_field_reasons)
    for stage in refreshed:
        if (result.get(f"{stage}_check") or {}).get("reliable"):
            remove.update(_RESOLVED_REASONS[stage])
    for key in ("review_reasons", "stage_review_reasons"):
        if key in result:
            result[key] = [reason for reason in result[key] if reason not in remove]
    result = refresh_printed_fields_result(result)
    if not refreshed and not accepted_fields and result == current:
        return result
    result["review_issues"] = migrate_issues(result)
    if "handwriting_selection" in result:
        result["review_issues"] = [item for item in result["review_issues"] if not _handwriting_conflict(item)]
        if result["handwriting_selection"]["conflict"]:
            result["review_issues"].append(issue("provider_conflict", "handwriting", _HANDWRITING_CONFLICT))
    if "printed_field_selection" in result:
        result["review_issues"] = [item for item in result["review_issues"] if not _printed_conflict(item)]
        if result["printed_field_selection"]["conflicts"]:
            result["review_issues"].append(issue("provider_conflict", "fields", _PRINTED_CONFLICT))
    return finalize_result(result)
