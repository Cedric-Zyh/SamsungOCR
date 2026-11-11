"""Machine replay shares strict rules without inheriting human confirmation."""

from copy import deepcopy

import pytest

from receipt_ocr.application.document_evidence import evaluate_document_evidence
from receipt_ocr.domain.check_comparison import compare_machine_seals
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.provider_result_refresh import refresh_printed_fields_result, refresh_provider_result
from receipt_ocr.review import _review_seal


def saved_document(stage, payload, requirement, method="paddle_v6"):
    name, key = ("要求到货", "required") if stage == "date" else ("签章要求", "requirement")
    return {"fields": {name: payload[f"{stage}_check"].get(key, "")},
            f"{stage}_check": deepcopy(payload[f"{stage}_check"]),
            "recognition_config": {"acceptance": {f"{stage}_match_mode": "any"}},
            "recognition_variants": {
                "fields": [{"method": "paddle_v6", "details": {"fields": {name: requirement}}}],
                stage: [{"method": method, "details": deepcopy(payload)}]},
            "review_reasons": [], "stage_review_reasons": []}


def replay(document, stage):
    results = {name: [{"method": item["method"], "result": item["details"]} for item in variants]
               for name, variants in document["recognition_variants"].items()}
    outcomes, _ = evaluate_document_evidence(results, document["fields"], document["recognition_config"], recompare=True)
    return outcomes[stage][0].payload[f"{stage}_check"]


@pytest.mark.parametrize("texts,status", [
    (["测试有限公司收货专用章多"], "不匹配"),
    (["收货专用章", "测试有限公司收货专用章多"], "不匹配"),
    (["收货专用章", "测试有限公司收货专用章"], "匹配"),
])
@pytest.mark.parametrize("refresh", [refresh_printed_fields_result, refresh_provider_result])
def test_local_strict_comparison_is_identical_for_fresh_replay_and_saved_refresh(texts, status, refresh):
    required = "测试有限公司收货专用章"
    old = compare_seal_text_strict("旧公司收货专用章", texts, mismatch_over_partial=True)
    old.update(backend="本地 PaddleOCR", recognition_mode="local", vendor_raw={"unknown": [1, None]})
    document = saved_document("seal", {"seal_check": old}, required)
    before = deepcopy(document)
    fresh = compare_machine_seals(required, texts, local=True)
    deferred = replay(document, "seal")
    saved = refresh(document)["seal_check"]
    assert fresh["status"] == deferred["status"] == saved["status"] == status
    assert deferred["reliable"] == saved["reliable"] == fresh["reliable"]
    assert deferred["comparison_policy"] == fresh["comparison_policy"]
    assert deferred["vendor_raw"] == old["vendor_raw"]
    assert document == before


def test_requirement_edit_does_not_turn_local_strict_text_into_a_fuzzy_match():
    required = "测试有限公司收货专用章"
    old = compare_machine_seals("旧公司收货专用章", [required + "多"], local=True)
    old["reliable"] = False
    fields, previous_fields = {"签章要求": required}, {"签章要求": old["requirement"]}
    reviewed = _review_seal(old, fields, previous_fields, {})
    assert reviewed["status"] == "不匹配" and reviewed["reliable"] is False
    assert "human_confirmed_match" not in reviewed
    confirmed = _review_seal(old, fields, previous_fields, {"seal_confirmed_match": True})
    assert confirmed["status"] == "匹配" and confirmed["human_confirmed_match"] is True
    assert confirmed["source"] == "人工复核"


@pytest.mark.parametrize("raw,status", [(None, "需人工复核"), ("11月10日", "需人工复核"),
                                       ("2026年11月10日", "匹配")])
@pytest.mark.parametrize("reliable", [False, True])
def test_changed_date_requires_independent_complete_observation_in_both_machine_entries(raw, status, reliable):
    payload = {"date_check": {"required": "2026-11-09", "actual": "2026-11-10",
                               "status": "不匹配", "reliable": reliable}}
    if raw is not None:
        payload["date_ocr_texts"] = [raw]
    document = saved_document("date", payload, "2026-11-10")
    deferred, saved = replay(document, "date"), refresh_printed_fields_result(document)["date_check"]
    assert deferred["status"] == saved["status"] == status
    assert deferred["reliable"] == saved["reliable"] == (reliable and status == "匹配")


@pytest.mark.parametrize("stage", ["date", "seal"])
def test_machine_entries_preserve_human_confirmed_variant_even_if_aggregate_is_old(stage):
    check = ({"required": "2026-11-09", "actual": "2026-11-10", "human_confirmed": True}
             if stage == "date" else {"requirement": "旧公司收货章", "recognized": "测试公司收货章",
                                       "human_confirmed_match": True})
    check.update(status="匹配", reliable=True, source="人工复核")
    document = saved_document(stage, {f"{stage}_check": check}, "2026-11-10" if stage == "date" else "测试公司收货章")
    document[f"{stage}_check"] = {"status": "需人工复核", "reliable": False}
    assert replay(document, stage) == check
    assert refresh_printed_fields_result(document) == document
    assert refresh_provider_result(document) == document


@pytest.mark.parametrize("marker", [{"pending": True}, {"error": "failed"}, {"simulated": True}])
@pytest.mark.parametrize("stage", ["date", "seal"])
def test_inactive_variant_cannot_be_revived_by_aggregate_completed_page_trace(stage, marker):
    check = ({"required": "2026-11-09", "actual": "2026-11-10"} if stage == "date"
             else {"requirement": "旧公司收货章", "recognized": "测试公司收货章"})
    check.update(status="不匹配", reliable=False, backend="单证通", simulated=False)
    required = "2026-11-10" if stage == "date" else "测试公司收货章"
    document = saved_document(stage, {f"{stage}_check": check}, required, "danzhengtong")
    document["recognition_variants"][stage][0].update(marker)
    document["danzhengtong"] = {"mode": "real", "simulated": False, "status": "completed",
                                "result": {"commitResult": {"签收日期": {"value": "2026-11-10"},
                                                           "收货客户印章": {"value": "测试公司收货章"}}}}
    updated = refresh_printed_fields_result(document)
    assert updated[f"{stage}_check"]["status"] == "需人工复核"
    assert updated[f"{stage}_check"]["reliable"] is False
    document["recognition_variants"].pop("fields")
    refreshed = refresh_provider_result(document)
    assert refreshed[f"{stage}_check"] == document[f"{stage}_check"]
