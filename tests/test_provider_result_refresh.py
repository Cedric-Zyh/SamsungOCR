from copy import deepcopy
import json

import pytest

from receipt_ocr.database import Database
from receipt_ocr.provider_result_refresh import REFRESH_ACTION, refresh_provider_result
from tools.refresh_provider_results import refresh_database


def saved_result():
    requirement = "太原市伊加壹电子服务总汇"
    trace = {"mode": "real", "simulated": False, "status": "completed", "result": {"commitResult": {
        "签收日期": {"value": "2025-02-10"}, "收货客户印章": {"value": requirement + " 商品收讫章"},
    }}}
    date = {"required": "2025-02-10", "actual": "2025-02-10", "status": "匹配",
            "reliable": False, "backend": "单证通", "simulated": False}
    seal = {"requirement": requirement, "recognized": "太原市伊服壹电子服务总汇", "status": "部分匹配",
            "reliable": False, "backend": "清瞳印章 API", "dual_check": {"selected": {"ocr": {"matched": True}}},
            "api": {"ok": True, "response": {"data": {"img_0": [{
                "text_formatted": "太原市伊服壹电子服务总汇",
                "matched_seal": {"label": "深圳欧瑞特供应链管理有限公司石家庄分公司收货专用章", "similarity": 0.843},
            }]}}}}
    dzt_seal = {"requirement": requirement, "recognized": requirement + " 商品收讫章", "status": "匹配",
                "reliable": False, "backend": "单证通", "simulated": False}
    result = {
        "filename": "7272433468.jpg", "overall": "需人工复核", "final_result": "需人工复核", "review_status": "待复核",
        "fields": {"要求到货": "2025-02-10", "签章要求": requirement}, "field_metadata": {},
        "date_check": date, "seal_check": seal, "danzhengtong": trace,
        "review_reasons": ["仅单证通识别，文档版式待复核", "单证通日期/印章为文字提取结果，需人工核对原图",
                           "印章内容无法可靠判断", "签收日期尚未可靠识别", "客户印章尚未可靠识别",
                           "部分识别：未执行项目不能据此判定整单通过"],
        "recognition_variants": {
            "date": [{"method": "danzhengtong", "details": {"date_check": deepcopy(date), "danzhengtong": trace}}],
            "seal": [{"method": "qingtong", "details": {"seal_check": deepcopy(seal)}},
                     {"method": "danzhengtong", "details": {"seal_check": dzt_seal, "danzhengtong": trace}}],
        },
    }
    return deepcopy(result)


def saved_field_result():
    reason = "仅单证通识别，文档版式待复核"
    fields = {"客户名称": "测试客户", "要求到货": "2025-02-10", "签章要求": "测试客户收货章"}
    trace = {"mode": "real", "simulated": False, "status": "completed",
             "result": {"commitResult": {name: {"value": value} for name, value in fields.items()}}}
    metadata = {name: {"source": "单证通", "simulated": False, "low_confidence": False}
                for name in fields}
    return {
        "filename": "accepted-fields.jpg", "fields": fields, "field_metadata": metadata,
        "document_type": {"type": "unclassified", "label": "未执行文档类型识别",
                          "reliable": False, "confidence": 0, "reasons": [reason]},
        "date_check": {"required": "2025-02-10", "actual": "2025-02-10", "status": "匹配", "reliable": True},
        "seal_check": {"requirement": "测试客户收货章", "recognized": "测试客户收货章", "status": "匹配", "reliable": True},
        "danzhengtong": deepcopy(trace),
        "recognition_config": {"fields": ["danzhengtong"], "products": ["vision"], "date": ["vision"], "seal": ["vision"]},
        "recognition_variants": {"fields": [{"method": "danzhengtong", "details": {
            "fields": deepcopy(fields), "field_metadata": deepcopy(metadata), "danzhengtong": deepcopy(trace)}}]},
        "review_reasons": [reason], "stage_review_reasons": [reason],
        "overall": "需人工复核", "final_result": "需人工复核", "review_status": "待复核",
    }


def test_saved_real_fields_acceptance_resolves_only_provider_layout_review():
    current = saved_field_result()
    before = deepcopy(current)
    updated = refresh_provider_result(current)
    assert current == before
    assert updated["fields"] == before["fields"]
    assert updated["field_metadata"] == before["field_metadata"]
    assert updated["danzhengtong"] == before["danzhengtong"]
    assert updated["recognition_variants"] == before["recognition_variants"]
    assert updated["date_check"] == before["date_check"]
    assert updated["seal_check"] == before["seal_check"]
    assert updated["document_type"] == {**before["document_type"], "reasons": [],
        "provider_fields_accepted": True, "source": "单证通", "acceptance_policy": "temporary_trust"}
    assert updated["review_reasons"] == updated["stage_review_reasons"] == []
    assert updated["final_result"] == "通过" and updated["review_status"] == "无需复核"
    assert refresh_provider_result(updated) == updated


def test_legacy_optional_stage_notice_recalculates_without_provider_refresh():
    current = {
        "date_check": {"required": "2025-02-10", "actual": "2025-02-10",
                        "status": "匹配", "reliable": True},
        "seal_check": {"requirement": "测试客户收货章", "recognized": "测试客户收货章",
                        "status": "匹配", "reliable": True},
        "review_reasons": ["部分识别：未执行项目不能据此判定整单通过"],
        "stage_review_reasons": ["部分识别：未执行项目不能据此判定整单通过"],
        "overall": "需人工复核", "final_result": "需人工复核", "review_status": "待复核",
    }
    updated = refresh_provider_result(current)
    assert updated["review_reasons"] == updated["stage_review_reasons"] == []
    assert updated["overall"] == updated["final_result"] == "通过"
    assert updated["review_status"] == "无需复核"


@pytest.mark.parametrize("reason", ["缺少必要字段：客户订单号", "存在低置信度字段",
    "部分识别：未执行项目不能据此判定整单通过", "客户印章尚未可靠识别", "单证通模拟接入未验证文档类型"])
def test_real_fields_acceptance_keeps_other_review_requirements(reason):
    current = saved_field_result()
    current["review_reasons"].append(reason)
    updated = refresh_provider_result(current)
    if reason == "部分识别：未执行项目不能据此判定整单通过":
        assert updated["review_reasons"] == []
        assert updated["final_result"] == "通过"
    else:
        assert updated["review_reasons"] == [reason]
        assert updated["final_result"] == "需人工复核"


@pytest.mark.parametrize("stage", ["date", "seal"])
def test_real_fields_acceptance_does_not_override_mismatching_date_or_seal(stage):
    current = saved_field_result()
    current[f"{stage}_check"]["status"] = "不匹配"
    updated = refresh_provider_result(current)
    assert updated[f"{stage}_check"] == current[f"{stage}_check"]
    # Mismatches are retained as review evidence until a human confirms them.
    assert updated["final_result"] == "需人工复核"


@pytest.mark.parametrize("stage", ["date", "seal"])
def test_real_fields_acceptance_cannot_pass_unreliable_date_or_seal(stage):
    current = saved_field_result()
    current[f"{stage}_check"].update(status="未识别", reliable=False)
    updated = refresh_provider_result(current)
    assert updated[f"{stage}_check"] == current[f"{stage}_check"]
    assert updated["final_result"] == "需人工复核"


@pytest.mark.parametrize("variant_change", ["simulated", "failed", "empty", "error", "simulated_metadata"])
def test_unverified_or_empty_field_evidence_does_not_remove_layout_review(variant_change):
    current = saved_field_result()
    variant = current["recognition_variants"]["fields"][0]
    details = variant["details"]
    if variant_change == "simulated":
        details["danzhengtong"].update(mode="mock", simulated=True)
    elif variant_change == "failed":
        details["danzhengtong"]["status"] = "failed"
    elif variant_change == "empty":
        details["fields"] = {"客户名称": " "}
    elif variant_change == "error":
        variant["error"] = "识别失败"
    else:
        details["field_metadata"]["客户名称"]["simulated"] = True
    assert refresh_provider_result(current) == current


def test_real_date_trace_alone_does_not_accept_unexecuted_fields():
    current = saved_field_result()
    current["recognition_variants"] = {}
    current["recognition_config"]["fields"] = []
    assert refresh_provider_result(current) == current


def test_legacy_fields_without_variants_require_provider_provenance():
    current = saved_field_result()
    current.pop("recognition_variants")
    assert refresh_provider_result(current)["final_result"] == "通过"
    for metadata in current["field_metadata"].values():
        metadata["source"] = "Vision"
    assert refresh_provider_result(current) == current


@pytest.mark.parametrize("stage_status", ["未执行", "识别失败"])
def test_legacy_failed_or_unexecuted_field_stage_keeps_layout_review(stage_status):
    current = saved_field_result()
    current.pop("recognition_variants")
    current["recognition_status"] = {"fields": stage_status}
    assert refresh_provider_result(current) == current


def test_legacy_handwriting_provenance_does_not_substitute_for_printed_fields():
    current = saved_field_result()
    current.pop("recognition_variants")
    current["fields"] = {"仓库接收人": "收货人"}
    current["field_metadata"] = {"仓库接收人": {"source": "单证通", "simulated": False}}
    assert refresh_provider_result(current) == current


def test_fields_acceptance_keeps_classified_document_and_unrelated_layout_reasons():
    current = saved_field_result()
    current["document_type"].update(type="warehouse_authorization", reliable=True, confidence=.98)
    reason = "仓库货物接收委托书不适用回单日期/印章模板"
    current["document_type"]["reasons"].append(reason)
    current["review_reasons"].append(reason)
    updated = refresh_provider_result(current)
    assert updated["document_type"]["type"] == "warehouse_authorization"
    assert updated["document_type"]["reasons"] == current["document_type"]["reasons"]
    assert updated["document_type"]["confidence"] == .98
    assert updated["review_reasons"] == current["review_reasons"]
    assert updated["final_result"] == "需人工复核"


def test_fields_acceptance_preserves_human_confirmation():
    current = saved_field_result()
    current.update(review_status="确认不通过", final_result="不通过")
    assert refresh_provider_result(current) == current


def test_refresh_exact_screenshot_evidence_and_keep_provider_trace():
    current = saved_result()
    before = deepcopy(current)
    updated = refresh_provider_result(current)
    assert current == before
    assert updated["danzhengtong"] == before["danzhengtong"]
    assert updated["seal_check"]["api"] == before["seal_check"]["api"]
    assert updated["date_check"]["reliable"] is True
    assert updated["date_check"]["source"] == "单证通"
    assert updated["seal_check"]["status"] == "不匹配"
    assert updated["seal_check"]["dual_check"]["selected"]["ocr"]["matched"] is False
    assert updated["recognition_variants"]["seal"][1]["details"]["seal_check"]["status"] == "不匹配"
    assert updated["review_reasons"] == ["仅单证通识别，文档版式待复核"]
    assert updated["overall"] == "需人工复核"
    assert refresh_provider_result(updated) == updated


def test_dzt_full_match_wins_and_preserves_all_raw_provider_evidence():
    current = saved_result()
    requirement = current["fields"]["签章要求"]
    current["danzhengtong"]["result"]["commitResult"]["收货客户印章"]["value"] = requirement
    current["review_reasons"].append("客户印章：多种识别方式结果不一致")
    before = deepcopy(current)
    updated = refresh_provider_result(current)
    assert updated["seal_check"]["recognized"] == requirement
    assert updated["seal_check"]["status"] == "匹配"
    assert updated["seal_check"]["reliable"] is True
    assert "单证通" in updated["seal_check"]["source"]
    assert updated["seal_check"]["api"] == before["seal_check"]["api"]
    assert updated["danzhengtong"] == before["danzhengtong"]
    assert updated["recognition_variants"]["seal"][0]["details"]["seal_check"]["status"] == "不匹配"
    assert "客户印章：多种识别方式结果不一致" not in updated["review_reasons"]
    assert refresh_provider_result(updated) == updated


def test_reaggregate_when_saved_individual_provider_checks_are_unchanged():
    current = saved_result()
    current["danzhengtong"]["result"]["commitResult"]["收货客户印章"]["value"] = current["fields"]["签章要求"]
    refreshed = refresh_provider_result(current)
    old_aggregate = deepcopy(refreshed)
    old_aggregate["seal_check"] = deepcopy(refreshed["recognition_variants"]["seal"][0]["details"]["seal_check"])
    updated = refresh_provider_result(old_aggregate)
    assert updated["recognition_variants"] == old_aggregate["recognition_variants"]
    assert updated["seal_check"] == refreshed["seal_check"]
    assert updated["seal_check"]["status"] == "匹配"


def test_best_partial_text_is_shown_when_no_provider_fully_matches():
    current = saved_result()
    partial = "太原市伊加壹电子服务"
    current["danzhengtong"]["result"]["commitResult"]["收货客户印章"]["value"] = partial
    updated = refresh_provider_result(current)
    assert updated["seal_check"]["recognized"] == partial
    assert updated["seal_check"]["status"] == "部分匹配"
    assert updated["seal_check"]["reliable"] is False


def test_latest_screenshot_displays_more_complete_dzt_text_despite_lower_ratio():
    current = saved_result()
    requirement = "合肥佳元电子第一分公司手机售后专用章"
    qing_text = "合肥佳元电子第分公司上"
    dzt_text = "合肥佳元电子通讯产品技术服务有限公司第一分公司 手机售后专用章 2025年02月11日"
    current["fields"]["签章要求"] = requirement
    current["seal_check"]["requirement"] = requirement
    for variant in current["recognition_variants"]["seal"]:
        variant["details"]["seal_check"]["requirement"] = requirement
    qing = current["recognition_variants"]["seal"][0]["details"]["seal_check"]
    qing["recognized"] = qing_text
    qing["api"]["response"]["data"]["img_0"][0]["text_formatted"] = qing_text
    current["danzhengtong"]["result"]["commitResult"]["收货客户印章"]["value"] = dzt_text
    updated = refresh_provider_result(current)
    variants = updated["recognition_variants"]["seal"]
    assert variants[0]["details"]["seal_check"]["score"] > variants[1]["details"]["seal_check"]["score"]
    assert updated["seal_check"]["recognized"] == dzt_text
    assert updated["seal_check"]["source"] == "单证通"
    assert updated["seal_check"]["status"] == "不匹配"
    assert updated["seal_check"]["requirement_coverage"] == 1.0
    assert refresh_provider_result(updated) == updated


@pytest.mark.parametrize("edit", [
    {"review_status": "确认通过"}, {"human_note": "已核对"},
    {"field_metadata": {"客户名称": {"source": "OCR + 人工复核"}}},
    {"date_check": {"source": "人工复核"}}, {"seal_check": {"human_confirmed_match": False}},
    {"page_review_override": {"fields": {"客户名称": "人工更正"}}},
])
def test_refresh_preserves_human_review_or_edits(edit):
    current = saved_result()
    current.update(edit)
    assert refresh_provider_result(current) == current


def test_simulated_dates_remain_unreliable():
    current = saved_result()
    current["danzhengtong"].update(mode="mock", simulated=True)
    current["recognition_variants"]["date"][0]["details"]["danzhengtong"].update(mode="mock", simulated=True)
    updated = refresh_provider_result(current)
    assert updated["date_check"]["reliable"] is False


def test_multiple_date_providers_keep_disagreement_for_review():
    current = saved_result()
    current["recognition_variants"]["date"].insert(0, {"method": "local", "details": {
        "date_check": {"required": "2025-02-10", "actual": "2025-02-11", "status": "不匹配", "reliable": True}}})
    updated = refresh_provider_result(current)
    assert updated["date_check"]["status"] == "需人工复核"
    assert updated["date_check"]["reliable"] is False


def test_database_dry_run_then_audited_apply_preserves_original(tmp_path):
    path = tmp_path / "results.db"
    database = Database(path)
    database.initialize()
    current = saved_result()
    result_id = database.insert_result(filename=current["filename"], stored_name="saved.jpg",
        preview_name="saved.jpg", task_id="", result=current)
    with database.connect() as connection:
        original = connection.execute("SELECT original_result_json FROM results WHERE id=?", (result_id,)).fetchone()[0]
    assert refresh_database(path, ids=[result_id])["counts"]["changed"] == 1
    assert database.history(result_id) == []
    assert database.get_result(result_id)["date_check"]["reliable"] is False
    assert refresh_database(path, ids=[result_id], apply=True)["counts"]["changed"] == 1
    assert database.get_result(result_id)["date_check"]["reliable"] is True
    history = database.history(result_id)
    assert len(history) == 1 and history[0]["action"] == REFRESH_ACTION
    assert json.loads(history[0]["before_json"])["date_check"]["reliable"] is False
    with database.connect() as connection:
        assert connection.execute("SELECT original_result_json FROM results WHERE id=?", (result_id,)).fetchone()[0] == original
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"unchanged": 1}


def test_database_preserves_draft_history_even_without_human_markers(tmp_path):
    path = tmp_path / "results.db"
    database = Database(path)
    database.initialize()
    current = saved_result()
    result_id = database.insert_result(filename=current["filename"], stored_name="saved.jpg",
        preview_name="saved.jpg", task_id="", result=current)
    database.review_result(result_id, result=current, review_status="待复核", final_result="需人工复核",
        note="", action="保存草稿")
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"human_preserved": 1}
    assert database.get_result(result_id)["date_check"]["reliable"] is False
    assert len(database.history(result_id)) == 1


def test_database_field_acceptance_preview_and_apply_are_auditable_and_idempotent(tmp_path):
    path = tmp_path / "results.db"
    database = Database(path)
    database.initialize()
    current = saved_field_result()
    result_id = database.insert_result(filename=current["filename"], stored_name="saved.jpg",
        preview_name="saved.jpg", task_id="", result=current)
    preview = refresh_database(path, ids=[result_id])
    change = preview["changes"][0]
    assert set(change["stages"]) == {"fields"}
    assert change["review_reasons"]["after"] == []
    assert change["verdict"] == {"before": ["需人工复核", "待复核"], "after": ["通过", "无需复核"]}
    assert database.get_result(result_id)["review_status"] == "待复核"
    assert database.history(result_id) == []
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"changed": 1}
    assert database.get_result(result_id)["final_result"] == "通过"
    history = database.history(result_id)
    assert len(history) == 1 and history[0]["action"] == REFRESH_ACTION
    assert json.loads(history[0]["before_json"])["review_reasons"] == current["review_reasons"]
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"unchanged": 1}


def test_empty_record_selection_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="指定记录列表不能为空"):
        refresh_database(tmp_path / "results.db", ids=[], apply=True)
