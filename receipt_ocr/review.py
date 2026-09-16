"""Human edits preserve machine evidence until the corresponding item is reviewed."""

from copy import deepcopy

from .decision import decide_overall
from .field_schema import derive_signature_check, project_fields
from .parser import LOW_CONFIDENCE_THRESHOLD, compare_dates, compare_seal_text, parse_date, product_table_text
from .parsing_seals import compare_seal_text_strict


def confirmation_error(result, review_status, final_result):
    if review_status != "确认通过" and final_result != "通过":
        return ""
    date = result.get("date_check") or {}
    seal = result.get("seal_check") or {}
    acceptance = (result.get("recognition_config") or {}).get("acceptance") or {}
    missing = []
    if acceptance.get("date_match_mode") != "none" and not (
            date.get("actual") and date.get("status") == "匹配" and date.get("reliable") is True):
        missing.append("实际收货日期尚未可靠识别或与要求到货日期不一致")
    if acceptance.get("seal_match_mode") != "none" and not (
            seal.get("recognized") and seal.get("status") == "匹配" and seal.get("reliable") is True):
        missing.append("印章内容尚未可靠识别或与签章要求不一致")
    signature = result.get("signature_check") or {}
    if acceptance.get("signature_match_mode") in {"any", "all"} and not (
            signature.get("status") == "匹配" and signature.get("reliable") is True):
        missing.append("签名核对尚未匹配或完成确认")
    if result.get("overall") != "通过" and not missing:
        missing.append("整体核验结论尚未达到通过条件")
    return "确认通过前请补全并核对：" + "；".join(missing) if missing else ""


def _review_date(previous, fields, old_fields, payload):
    actual = str(payload.get("actual_date", (payload.get("fields") or {}).get(
        "签收日期", previous.get("actual", "")))).strip()
    changed = actual != str(previous.get("actual") or "").strip()
    confirmed = payload.get("actual_date_confirmed") is True
    required_changed = fields.get("要求到货", "") != old_fields.get("要求到货", "")
    if not (changed or confirmed or required_changed):
        return deepcopy(previous)
    parsed = parse_date(actual)
    check = {**deepcopy(previous), **compare_dates(fields.get("要求到货", ""), parsed)}
    if changed or confirmed:
        check.update(confidence=1.0 if parsed else 0.0, reliable=bool(parsed),
                     source="人工复核", human_confirmed=bool(parsed))
    return check


def _review_seal(previous, fields, old_fields, payload):
    text = str(payload.get("seal_text", previous.get("recognized", ""))).strip()
    changed = text != str(previous.get("recognized") or "").strip()
    required_changed = fields.get("签章要求", "") != old_fields.get("签章要求", "")
    human_match = payload.get("seal_confirmed_match") if "seal_confirmed_match" in payload else payload.get("truth_seal_should_match")
    confirmed = isinstance(human_match, bool) and (bool(text) or human_match is False)
    if not (changed or required_changed or confirmed):
        return deepcopy(previous)
    compare = (compare_seal_text_strict if previous.get("dual_check")
               or previous.get("recognition_mode") == "danzhengtong"
               or previous.get("backend") == "单证通" else compare_seal_text)
    check = {**deepcopy(previous), **compare(fields.get("签章要求", ""), [text] if text else [])}
    check.pop("human_confirmed_match", None)
    if confirmed:
        check.update(status="匹配" if human_match else "不匹配", score=1.0,
                     message="人工复核确认印章与签章要求" + ("一致" if human_match else "不一致"),
                     human_confirmed_match=human_match, confidence=1.0, reliable=True,
                     source="人工复核", backend="人工复核")
    elif previous.get("dual_check") or "human_confirmed_match" in previous:
        # Editing text cannot settle a disagreement or transfer a previous
        # explicit judgement to a different stamp/requirement.
        check.update(recognized=text, status="需人工复核", reliable=False,
                     message="印章信息已修改，等待人工确认")
    elif changed:
        check.update(confidence=1.0 if text else 0.0, reliable=bool(text) and check['status'] in {'匹配', '不匹配'},
                     source="人工复核", backend="人工复核")
    return check


def _review_signature(previous, fields, old_fields, payload):
    """Apply the optional human signature comparison without gating pass."""
    current = derive_signature_check(fields, previous)
    old = derive_signature_check(old_fields, previous)
    changed = (
        current.get("contact") != old.get("contact")
        or current.get("receiver") != old.get("receiver")
    )
    human_match = payload.get("signature_confirmed_match")
    if isinstance(human_match, bool):
        current.update(
            status="匹配" if human_match else "不匹配",
            reliable=True,
            source="人工复核",
            human_confirmed_match=human_match,
        )
    elif changed:
        current.pop("human_confirmed_match", None)
        current["source"] = "字段比对"
    return current


def _review_fields(result, payload, *, confirm_fields):
    fields = result.setdefault("fields", {})
    metadata = result.setdefault("field_metadata", {})
    for name, value in (payload.get("fields") or {}).items():
        value = str(value).strip()
        previous = fields.get(name, "")
        fields[name] = value
        if name == "签收日期":
            continue  # Date evidence has its own explicit confirmation.
        if value != previous or confirm_fields:
            meta = dict(metadata.get(name, {}))
            meta.update(original=meta.get("original", previous), value=value,
                        confidence=1.0, low_confidence=False, source="人工复核")
            metadata[name] = meta
    table = result.get("product_table") or {}
    rows = table.get("rows") or []
    columns = set(table.get("columns") or [])
    changed_table = False
    for edit in payload.get("product_rows") or []:
        index = int(edit.get("row", -1))
        column = str(edit.get("column", ""))
        if index < 0 or index >= len(rows) or column not in columns:
            continue
        row = rows[index]
        value = str(edit.get("value", "")).strip()
        previous = str(row.get("values", {}).get(column, ""))
        if value == previous and not confirm_fields:
            continue
        changed_table = True
        row.setdefault("original_values", {}).setdefault(column, previous)
        row.setdefault("values", {})[column] = value
        row.setdefault("confidences", {})[column] = 1.0
        row.setdefault("sources", {})[column] = "人工复核"
        row["low_confidence_columns"] = [name for name in row.get("low_confidence_columns", []) if name != column]
        scores = [score for name, score in row["confidences"].items() if row["values"].get(name)]
        row["row_confidence"] = round(sum(scores) / max(1, len(scores)), 3)
    if changed_table:
        scores = [score for row in rows for name, score in row.get("confidences", {}).items() if row["values"].get(name)]
        table["confidence"] = round(sum(scores) / max(1, len(scores)), 3)
        fields["商品明细原文"] = product_table_text(table)
        metadata["商品明细原文"] = {
            **metadata.get("商品明细原文", {}), "value": fields["商品明细原文"],
            "confidence": table["confidence"], "low_confidence": table["confidence"] < LOW_CONFIDENCE_THRESHOLD,
            "source": "商品表格按列识别 + 人工复核",
        }


def apply_human_edits(current, payload):
    result = deepcopy(current)
    old_fields = deepcopy(current.get("fields") or {})
    confirm_fields = payload.get("review_status") in {"确认通过", "确认不通过"}
    _review_fields(result, payload, confirm_fields=confirm_fields)
    fields = result["fields"]
    date = result["date_check"] = _review_date(current.get("date_check") or {}, fields, old_fields, payload)
    seal = result["seal_check"] = _review_seal(current.get("seal_check") or {}, fields, old_fields, payload)
    signature = result["signature_check"] = _review_signature(
        current.get("signature_check") or {}, fields, old_fields, payload
    )
    if date.get("human_confirmed"):
        result["field_metadata"]["签收日期"] = {
            **result["field_metadata"].get("签收日期", {}), "value": date.get("actual", ""),
            "confidence": date.get("confidence", 0), "low_confidence": not date.get("reliable"), "source": "人工复核",
        }
    evidence_changed = (fields != old_fields or date != (current.get("date_check") or {})
                        or seal != (current.get("seal_check") or {})
                        or signature != (current.get("signature_check") or {})
                        or result.get("product_table") != current.get("product_table"))
    if not (evidence_changed or confirm_fields):
        return project_fields(result)
    # Saving a draft is not acknowledgement of unrelated routing/field warnings.
    reasons = [] if confirm_fields else list(current.get("review_reasons") or [])
    if any(meta.get("low_confidence") for name, meta in result["field_metadata"].items()
           if name != "仓库接收人"):
        reasons.append("存在低置信度字段")
    if not date.get("reliable"):
        reasons.append("收货日期尚未可靠识别或人工确认")
    if not seal.get("reliable"):
        reasons.append("印章内容尚未可靠识别或人工确认")
    result["review_reasons"] = list(dict.fromkeys(reasons))
    result["overall"] = decide_overall(
        date, seal, result["review_reasons"],
        (result.get("recognition_config") or {}).get("acceptance"), signature,
    )
    # Date mismatches are intentionally kept pending until a human confirms
    # them.  Once the reviewer explicitly chooses "确认不通过", persist that
    # decision as the overall conclusion rather than leaving the machine
    # policy's pending value in the stored payload.
    if (payload.get("review_status") == "确认不通过"
            and payload.get("final_result") == "不通过"):
        result["overall"] = "不通过"
    return project_fields(result)


def prepare_review_payload(stored, reviewed, *, review_status, final_result, note="", error_type=""):
    """Keep physical pages intact; merged edits live in the cover override."""
    if (reviewed.get("page_group") or {}).get("page_count", 0) <= 1 or reviewed.get("page_role") == "continuation":
        return deepcopy(reviewed)
    override = {key: deepcopy(reviewed[key]) for key in (
        "fields", "field_metadata", "product_table", "date_check", "seal_check", "signature_check", "review_reasons", "overall"
    ) if key in reviewed}
    override.update(review_status=review_status, final_result=final_result, human_note=note, error_type=error_type)
    result = deepcopy(stored)
    result.update(page_review_override=override, overall=reviewed.get("overall", "需人工复核"))
    return result
