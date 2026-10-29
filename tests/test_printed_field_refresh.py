"""Saved printed-field choices retain provenance and invalidate stale checks."""

from copy import deepcopy

import pytest

from receipt_ocr.provider_result_refresh import refresh_printed_fields_result, refresh_provider_result


CONFLICT = "印刷字段：多种识别方式结果不一致"


def provider(method, fields, **metadata):
    source = "单证通" if method == "danzhengtong" else method
    details = {"fields": deepcopy(fields), "field_metadata": {
        name: {"value": str(value), "original": str(value), "source": source, **metadata}
        for name, value in fields.items()}}
    if method == "danzhengtong":
        details["danzhengtong"] = {"mode": "real", "status": "completed", "simulated": False}
    return {"method": method, "value": deepcopy(fields), "details": details}


def saved_result():
    fields = {"客户名称": "客户甲", "要求到货": "2025-01-04", "签章要求": "客户甲收货章（04）",
              "仓库联系人": "郭亮", "合计数量": "7"}
    local = provider("paddle_v6", fields, confidence=.95, low_confidence=False)
    remote = provider("danzhengtong", {**fields, "签章要求": "客户甲收货章(04)", "实收数量": "", "拒收数量": ""},
                      low_confidence=False)
    return {
        "fields": {**fields, "仓库接收人": "郭亮", "实收数量": "", "拒收数量": ""},
        "field_metadata": deepcopy(local["details"]["field_metadata"]),
        "recognition_variants": {"fields": [local, remote]},
        "date_check": {"required": "2025-01-04", "actual": "2025-01-04", "status": "匹配", "reliable": True},
        "seal_check": {"requirement": fields["签章要求"], "recognized": "客户甲收货章(04)", "status": "匹配", "reliable": True},
        "signature_check": {"contact": "郭亮", "receiver": "郭亮", "status": "匹配", "reliable": True, "source": "字段比对"},
        "recognition_config": {"acceptance": {"date_match_mode": "any", "seal_match_mode": "any", "signature_match_mode": "none"}},
        "review_issues": [{"code": "provider_conflict", "scope": "fields", "message": CONFLICT, "blocking": True, "provider": ""}],
        "review_reasons": [CONFLICT], "stage_review_reasons": [],
        "overall": "需人工复核", "final_result": "需人工复核", "review_status": "待复核",
    }


def select_replacement(current, name, value):
    old = current["recognition_variants"]["fields"][0]["details"]
    old["field_metadata"][name].update(confidence=.4, low_confidence=True)
    remote = current["recognition_variants"]["fields"][1]["details"]
    remote["fields"][name] = value
    remote["field_metadata"][name].update(value=value, original=value, low_confidence=False)


@pytest.mark.parametrize("refresh", [refresh_printed_fields_result, refresh_provider_result])
def test_equivalent_printed_fields_clear_old_conflict_without_rechecking_other_stages(refresh):
    current = saved_result()
    before = deepcopy(current)
    updated = refresh(current)
    assert current == before
    assert updated["recognition_variants"] == before["recognition_variants"]
    assert updated["date_check"] == before["date_check"]
    assert updated["seal_check"] == before["seal_check"]
    assert CONFLICT not in updated["review_reasons"]
    assert not updated["printed_field_selection"]["conflicts"]
    assert updated["fields"]["仓库接收人"] == "郭亮"
    assert refresh(updated) == updated


def test_selects_each_nonempty_field_with_its_provenance_and_keeps_unrelated_issues():
    current = saved_result()
    current["fields"]["客户名称"] = ""
    local = current["recognition_variants"]["fields"][0]["details"]
    local["fields"]["客户名称"] = ""
    remote = current["recognition_variants"]["fields"][1]["details"]
    remote["fields"]["仓库联系人"] = ""
    unrelated = {"code": "review_required", "scope": "document", "message": "版式待核对", "blocking": True, "provider": ""}
    current["review_issues"].append(unrelated)
    current["review_reasons"].append(unrelated["message"])
    updated = refresh_printed_fields_result(current)
    assert updated["fields"]["客户名称"] == "客户甲"
    assert updated["field_metadata"]["客户名称"]["source"] == "单证通"
    assert updated["fields"]["仓库联系人"] == "郭亮"
    assert updated["field_metadata"]["仓库联系人"]["source"] == "paddle_v6"
    assert updated["review_issues"] == [unrelated]
    assert updated["recognition_variants"] == current["recognition_variants"]


def test_missing_or_failed_candidates_do_not_erase_saved_fields():
    current = saved_result()
    current["fields"]["实收数量"] = "0"
    current["recognition_variants"]["fields"][1]["error"] = "连接失败"
    updated = refresh_printed_fields_result(current)
    assert updated["fields"]["实收数量"] == "0"


def test_legacy_same_value_source_fallback_survives_missing_variant_fallbacks():
    current = saved_result()
    fallback = {"value": "郭亮", "source": "paddle_v6", "reason": "从签收栏找到联系人"}
    current["field_fallbacks"] = {"仓库联系人": deepcopy(fallback)}
    updated = refresh_printed_fields_result(current)
    assert updated["field_fallbacks"]["仓库联系人"] == fallback
    assert refresh_printed_fields_result(updated) == updated


def test_replacing_field_removes_only_its_outdated_fallback():
    current = saved_result()
    current["field_fallbacks"] = {
        "客户名称": {"value": "客户甲", "source": "paddle_v6"},
        "仓库联系人": {"value": "郭亮", "source": "paddle_v6"},
    }
    select_replacement(current, "客户名称", "客户乙")
    updated = refresh_printed_fields_result(current)
    assert "客户名称" not in updated["field_fallbacks"]
    assert updated["field_fallbacks"]["仓库联系人"] == current["field_fallbacks"]["仓库联系人"]


def test_unresolved_real_field_disagreement_still_requires_review():
    current = saved_result()
    select_replacement(current, "客户名称", "客户乙")
    current["recognition_variants"]["fields"][0]["details"]["field_metadata"]["客户名称"].update(confidence=.95, low_confidence=False)
    updated = refresh_printed_fields_result(current)
    assert "客户名称" in updated["printed_field_selection"]["conflicts"]
    assert CONFLICT in updated["review_reasons"]
    assert updated["final_result"] == "需人工复核"


@pytest.mark.parametrize("change", [
    {"review_status": "确认通过"},
    {"human_note": "人工已检查"},
    {"field_metadata": {"客户名称": {"source": "人工复核"}}},
    {"signature_check": {"source": "人工复核", "human_confirmed_match": False}},
])
def test_preserves_human_review(change):
    current = saved_result()
    current.update(change)
    assert refresh_printed_fields_result(current) == current
    assert refresh_provider_result(current) == current


@pytest.mark.parametrize("mode", ["any", "all", "none"])
def test_changed_required_date_never_reuses_old_local_match(mode):
    current = saved_result()
    select_replacement(current, "要求到货", "2025-01-05")
    current["recognition_config"]["acceptance"]["date_match_mode"] = mode
    updated = refresh_printed_fields_result(current)
    assert updated["date_check"]["required"] == "2025-01-05"
    assert updated["date_check"]["status"] == ("未核对" if mode == "none" else "需人工复核")
    assert updated["date_check"]["reliable"] is (mode == "none")
    assert updated["recognition_variants"] == current["recognition_variants"]
    assert refresh_printed_fields_result(updated) == updated


@pytest.mark.parametrize("refresh", [refresh_printed_fields_result, refresh_provider_result])
def test_changed_required_date_recompares_real_provider_observation_and_stays_idempotent(refresh):
    current = saved_result()
    select_replacement(current, "要求到货", "2025-01-05")
    trace = {"mode": "real", "status": "completed", "simulated": False,
             "result": {"commitResult": {"签收日期": {"value": "2025-01-04"}}}}
    check = {**current["date_check"], "backend": "单证通", "simulated": False}
    current["date_check"] = deepcopy(check)
    current["recognition_variants"]["date"] = [{"method": "danzhengtong", "details": {"date_check": check, "danzhengtong": trace}}]
    updated = refresh(current)
    assert updated["date_check"]["required"] == "2025-01-05"
    assert updated["date_check"]["actual"] == "2025-01-04"
    assert updated["date_check"]["status"] == "不匹配"
    assert updated["date_check"]["reliable"] is True
    assert updated["final_result"] != "通过"
    assert refresh(updated) == updated


@pytest.mark.parametrize("mode, expected", [("any", "匹配"), ("all", "需人工复核"), ("none", "未核对")])
def test_recompared_date_retains_any_all_none_policy_with_two_providers(mode, expected):
    current = saved_result()
    select_replacement(current, "要求到货", "2025-01-05")
    current["recognition_config"]["acceptance"]["date_match_mode"] = mode
    trace = {"mode": "real", "status": "completed", "simulated": False,
             "result": {"commitResult": {"签收日期": {"value": "2025-01-05"}}}}
    current["recognition_variants"]["date"] = [
        {"method": "paddle_v6", "details": {"date_check": deepcopy(current["date_check"])}},
        {"method": "danzhengtong", "details": {
            "date_check": {**current["date_check"], "backend": "单证通"}, "danzhengtong": trace}},
    ]
    updated = refresh_printed_fields_result(current)
    assert updated["date_check"]["required"] == "2025-01-05"
    assert updated["date_check"]["status"] == expected
    assert updated["recognition_variants"] == current["recognition_variants"]
    assert refresh_printed_fields_result(updated) == updated


@pytest.mark.parametrize("refresh", [refresh_printed_fields_result, refresh_provider_result])
def test_fullwidth_required_date_and_real_actual_use_canonical_dates_but_keep_raw_text(refresh):
    current = saved_result()
    raw = "２０２５／０１／０５"
    select_replacement(current, "要求到货", raw)
    trace = {"mode": "real", "status": "completed", "simulated": False,
             "result": {"commitResult": {"签收日期": {"value": raw}}}}
    current["recognition_variants"]["date"] = [{"method": "danzhengtong", "details": {
        "date_check": {**current["date_check"], "backend": "单证通"}, "danzhengtong": trace}}]
    updated = refresh(current)
    assert updated["fields"]["要求到货"] == raw
    assert updated["date_check"]["required"] == updated["date_check"]["actual"] == "2025-01-05"
    assert updated["date_check"]["raw_text"] == raw
    assert updated["date_check"]["status"] == "匹配"
    assert refresh(updated) == updated


def test_changed_seal_requirement_recompares_saved_qingtong_channels_without_mutating_variants():
    current = saved_result()
    select_replacement(current, "签章要求", "客户乙收货章(04)")
    external = {"ok": True, "response": {"data": {"img_0": [{
        "text_formatted": "客户甲收货章(04)",
        "matched_seal": {"label": "客户甲收货章(04)", "similarity": .99}}]}}}
    check = {**current["seal_check"], "recognition_mode": "qingtong_only", "api": external,
             "dual_check": {"policy": "qingtong_any_channel"}}
    current["seal_check"] = deepcopy(check)
    current["recognition_variants"]["seal"] = [{"method": "qingtong", "details": {"seal_check": check}}]
    updated = refresh_printed_fields_result(current)
    assert updated["seal_check"]["requirement"] == "客户乙收货章(04)"
    assert updated["seal_check"]["status"] != "匹配"
    assert updated["final_result"] != "通过"
    assert updated["recognition_variants"] == current["recognition_variants"]
    assert refresh_printed_fields_result(updated) == updated


def test_missing_seal_evidence_invalidates_old_match_after_requirement_changes():
    current = saved_result()
    select_replacement(current, "签章要求", "客户乙收货章(04)")
    updated = refresh_printed_fields_result(current)
    assert updated["seal_check"]["status"] == "需人工复核"
    assert updated["seal_check"]["reliable"] is False


def test_replacing_only_low_confidence_candidate_clears_warning_but_preserves_other_warnings():
    current = saved_result()
    select_replacement(current, "客户名称", "客户乙")
    current["field_metadata"]["客户名称"]["low_confidence"] = True
    current["review_issues"].append({"code": "low_confidence", "scope": "fields", "message": "存在低置信度字段", "blocking": True, "provider": ""})
    updated = refresh_printed_fields_result(current)
    assert updated["fields"]["客户名称"] == "客户乙"
    assert not any(item["code"] == "low_confidence" for item in updated["review_issues"])
