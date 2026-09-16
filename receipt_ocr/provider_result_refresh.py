"""Reapply provider comparison rules to saved machine evidence without OCR calls."""

from copy import deepcopy

from .decision import OPTIONAL_STAGE_REASONS, finalize_result
from .field_schema import PRINTED_FIELDS
from .parser import compare_dates, parse_date
from .parsing_seals import compare_seal_text_strict
from .provider_field_policy import DZT_LAYOUT_REASONS, accept_real_dzt_fields
from .qingtong_seal import compare_qingtong_seal
from .seal_provider_policy import combine_seal_provider_checks


REFRESH_ACTION = "识别规则更新"
_OLD_DZT_REASON = "单证通日期/印章为文字提取结果，需人工核对原图"
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
    checks = [result.get("date_check") or {}, result.get("seal_check") or {}]
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
        rebuilt = compare_dates(check.get("required") or fields.get("要求到货", ""), parse_date(text))
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


def refresh_provider_result(current):
    """Return a copy with refreshed provider decisions; preserve all human records."""
    if has_human_edits(current):
        return deepcopy(current)
    result = deepcopy(current)
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
    if not refreshed and not accepted_fields and result == current:
        return result
    return finalize_result(result)
