"""Replay saved machine checks from validated observations, without I/O.

Provider response decoding belongs here; domain comparisons know nothing about
provider clients, persistence or human review. Both snapshot replay and saved
record refresh use these same rules.
"""

from copy import deepcopy

from ..domain.check_comparison import (STRICT_SEAL_POLICIES, compare_machine_date,
                                       compare_machine_seals)
from ..domain.evidence_selection import inactive
from ..domain.parsing import parse_date
from ..domain.printed_field_selection import canonical_printed_field
from ..domain.review_policy import has_human_edits
from ..recognition.seal.providers.qingtong import compare_qingtong_seal


def dedicated_seal_check(check, requirement, trace):
    """Recompare each physical stamp on its own; never join their strings."""
    if not (isinstance(trace.get("requests"), list)
            or (trace.get("model_id") and trace.get("model_id") == check.get("model_id"))):
        return None
    from ..danzhengtong_seal import normalize_seal_result

    candidates, metadata = [], []
    saved = check.get("candidates") or []
    for request in trace.get("requests") or []:
        if not isinstance(request, dict) or inactive(request) or inactive(request.get("result")):
            continue
        if (request.get("region") or {}).get("role") == "发货单位章":
            continue
        if request.get("status") not in {"completed", "received"}:
            continue
        commit = (request.get("result") or {}).get("commitResult")
        if not isinstance(commit, dict):
            continue
        normalized = normalize_seal_result(commit)
        fields = normalized["fields"]
        position = len(candidates)
        original = next((item for item in saved if isinstance(item, dict)
                         and request.get("index") is not None and item.get("index") == request["index"]),
                        saved[position] if position < len(saved) and isinstance(saved[position], dict) else {})
        candidates.append({**deepcopy(original), "index": request.get("index", original.get("index", position)),
                           "text": fields["收货客户印章"], "color": fields["印章颜色"],
                           "type": fields["印章类型"]})
        metadata.append(normalized["metadata"])
    # Saved per-stamp candidates remain usable when an older dedicated save
    # omitted its child traces. The selected display string is not a candidate
    # source, and full-page commitResult is deliberately never read here.
    if not candidates and "requests" not in trace:
        candidates = [deepcopy(item) for item in saved if isinstance(item, dict)
                      and isinstance(item.get("text"), str)
                      and item.get("role") != "发货单位章"
                      and (item.get("region") or {}).get("role") != "发货单位章"]
    rebuilt = compare_machine_seals(requirement, [item["text"] for item in candidates])
    selected = next((index for index, item in enumerate(candidates)
                     if item["text"] == rebuilt.get("recognized")), 0)
    item = candidates[selected] if candidates else {}
    rebuilt.update(recognition_mode="danzhengtong", model_id=trace.get("model_id") or check.get("model_id", ""),
                   provider_color=item.get("color", ""), provider_seal_type=item.get("type", ""),
                   candidates=candidates)
    if metadata:
        rebuilt["provider_metadata"] = deepcopy(metadata[selected])
    elif item.get("text") != check.get("recognized"):
        rebuilt["provider_metadata"] = {}
    return rebuilt


def _provider_comparison(evidence, requirement, acceptance, *, allow_saved_text=False):
    check, stage, trace = evidence.check, evidence.stage, evidence.trace
    if not evidence.active:
        return None
    if stage == "seal" and evidence.method == "qingtong":
        external = check.get("api") or {}
        if external.get("ok") is True and isinstance(external.get("response"), dict):
            return compare_qingtong_seal(requirement, external,
                                         match_mode=acceptance.get("seal_match_mode", "any"))
        return None
    if evidence.method != "danzhengtong" or not evidence.real_provider:
        return None
    if stage == "seal":
        dedicated = dedicated_seal_check(check, requirement, trace)
        if dedicated is not None:
            return dedicated
    commit = (trace.get("result") or {}).get("commitResult") or {}
    item = commit.get("签收日期" if stage == "date" else "收货客户印章") or {}
    text = item.get("value") if isinstance(item, dict) else None
    if text is None and allow_saved_text:
        text = (check.get("raw_text", check.get("actual", "")) if stage == "date"
                else check.get("recognized", ""))
    if text is None:
        return None
    text = str(text)
    if stage == "date":
        rebuilt = compare_machine_date(requirement, text)
        rebuilt.update(reliable=rebuilt["status"] in {"匹配", "不匹配"}, raw_text=text)
    else:
        rebuilt = compare_machine_seals(requirement, [text] if text else [])
    return rebuilt


def _independent_local_date(evidence):
    """A complete saved reading may support a date; a derived display cannot.

    Historical dates sometimes borrowed missing digits from the old printed
    requirement. Only an independently parseable observation of the selected
    date permits re-evaluation against a different requirement.
    """
    actual = parse_date(canonical_printed_field("要求到货", evidence.check.get("actual", "")))
    if actual is None:
        return None
    readings = [evidence.check.get("raw_text"), *(evidence.details.get("date_ocr_texts") or [])]
    return next((text for text in readings if isinstance(text, str)
                 and parse_date(canonical_printed_field("要求到货", text)) == actual), None)


def recompare_requirement(evidence, fields, acceptance=None):
    """Re-evaluate a changed requirement without granting new evidence trust."""
    check, stage = evidence.check, evidence.stage
    copied = deepcopy(check)
    if has_human_edits(evidence.details):
        return copied
    requirement = fields.get("要求到货" if stage == "date" else "签章要求", "")
    if stage == "date":
        requirement = canonical_printed_field("要求到货", requirement)
    copied["required" if stage == "date" else "requirement"] = requirement
    if not check or not evidence.active:
        return copied
    rebuilt = _provider_comparison(evidence, requirement, acceptance or {})
    if rebuilt is None and evidence.method not in {"danzhengtong", "qingtong"}:
        if stage == "seal" and check.get("comparison_policy") in STRICT_SEAL_POLICIES and check.get("recognized"):
            texts = check.get("all_recognized") or [check["recognized"]]
            rebuilt = compare_machine_seals(requirement, texts, local=True, reliable=check.get("reliable") is True)
        elif stage == "date" and (text := _independent_local_date(evidence)) is not None:
            rebuilt = compare_machine_date(requirement, text)
            rebuilt["reliable"] = check.get("reliable") is True and rebuilt["status"] in {"匹配", "不匹配"}
    if rebuilt is not None:
        return {**copied, **rebuilt}
    copied.update(status="需人工复核", reliable=False,
                  message="比对依据已更新，需重新核对" + ("签收日期" if stage == "date" else "客户印章"))
    return copied


def refresh_provider_check(evidence, fields):
    """Apply current rules to a completed provider response at its saved basis."""
    check = evidence.check
    if not check or not evidence.active or has_human_edits(evidence.details):
        return deepcopy(check)
    requirement = (check.get("required") or fields.get("要求到货", "") if evidence.stage == "date"
                   else check.get("requirement") or fields.get("签章要求", ""))
    rebuilt = _provider_comparison(evidence, requirement, {}, allow_saved_text=True)
    if rebuilt is None:
        return deepcopy(check)
    if evidence.method == "danzhengtong":
        rebuilt.update(simulated=False, backend="单证通", source="单证通")
        if evidence.stage == "seal":
            rebuilt["recognition_mode"] = "danzhengtong"
    return {**check, **rebuilt}
