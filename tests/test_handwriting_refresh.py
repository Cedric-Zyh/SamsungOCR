"""Saved handwriting selection must be repairable without rerunning providers."""

from copy import deepcopy
import json

import pytest

from receipt_ocr.persistence.database import Database
from receipt_ocr.provider_result_refresh import (
    REFRESH_ACTION, refresh_handwriting_result, refresh_provider_result,
)
from tools.analysis.operations.refresh_provider_results import refresh_database


CONFLICT = "手写签名：多种识别方式结果不一致"


def saved_handwriting_result():
    trace = {"mode": "real", "simulated": False, "status": "completed"}
    return {
        "filename": "7329213942.jpg",
        "fields": {"仓库联系人": "郭亮", "仓库接收人": "", "实收数量": "8", "拒收数量": "0"},
        "field_metadata": {},
        "recognition_status": {"handwriting": "已完成"},
        "recognition_variants": {"handwriting": [
            {"method": "paddle_v6", "value": {"仓库接收人": ""}, "details": {
                "handwriting_fields": {"仓库接收人": ""}, "handwriting_metadata": {}}},
            {"method": "danzhengtong", "value": {"仓库接收人": "郭亮", "实收数量": "", "拒收数量": ""},
             "details": {"handwriting_fields": {"仓库接收人": "郭亮", "实收数量": "", "拒收数量": ""},
                 "handwriting_metadata": {"仓库接收人": {"value": "郭亮", "original": "郭亮",
                     "source": "单证通", "confidence": None, "low_confidence": False,
                     "simulated": False, "provider_ratio": 88}}, "danzhengtong": trace}},
        ]},
        "review_reasons": [CONFLICT],
        "stage_review_reasons": [],
        "review_issues": [{"code": "provider_conflict", "scope": "handwriting", "message": CONFLICT,
                           "blocking": True, "provider": ""}],
        "date_check": {"status": "未识别", "reliable": False},
        "seal_check": {"status": "未识别", "reliable": False},
        "overall": "需人工复核", "final_result": "需人工复核", "review_status": "待复核",
    }


@pytest.mark.parametrize("refresh", [refresh_handwriting_result, refresh_provider_result])
def test_saved_nonempty_receiver_replaces_empty_primary_without_touching_other_fields(refresh):
    current = saved_handwriting_result()
    before = deepcopy(current)
    updated = refresh(current)
    assert current == before
    assert updated["fields"] == {**before["fields"], "仓库接收人": "郭亮"}
    assert updated["field_metadata"]["仓库接收人"] == before["recognition_variants"]["handwriting"][1]["details"]["handwriting_metadata"]["仓库接收人"]
    assert updated["recognition_variants"] == before["recognition_variants"]
    assert updated["date_check"] == before["date_check"]
    assert updated["seal_check"] == before["seal_check"]
    assert CONFLICT not in updated["review_reasons"]
    assert not any(item["code"] == "provider_conflict" for item in updated["review_issues"])
    assert updated["signature_check"]["receiver"] == "郭亮"
    assert updated["signature_check"]["status"] == "匹配"
    assert refresh(updated) == updated


def test_quantity_keys_and_receiver_whitespace_cannot_create_saved_conflict():
    current = saved_handwriting_result()
    paddle = current["recognition_variants"]["handwriting"][0]
    paddle["details"]["handwriting_fields"] = {"仓库接收人": "郭 亮"}
    paddle["value"] = {"仓库接收人": "郭 亮"}
    updated = refresh_provider_result(current)
    assert CONFLICT not in updated["review_reasons"]
    assert updated["fields"]["实收数量"] == "8"
    assert updated["fields"]["拒收数量"] == "0"


def test_empty_receivers_with_extra_quantity_keys_do_not_disagree():
    current = saved_handwriting_result()
    details = current["recognition_variants"]["handwriting"][1]["details"]
    details["handwriting_fields"]["仓库接收人"] = ""
    details["handwriting_metadata"] = {}
    updated = refresh_handwriting_result(current)
    assert updated["fields"]["仓库接收人"] == ""
    assert CONFLICT not in updated["review_reasons"]
    assert updated["handwriting_selection"]["selected_method"] is None
    assert refresh_handwriting_result(updated) == updated


def test_true_unresolved_name_disagreement_keeps_structured_handwriting_issue():
    current = saved_handwriting_result()
    paddle = current["recognition_variants"]["handwriting"][0]
    paddle["details"]["handwriting_fields"] = {"仓库接收人": "李明"}
    paddle["details"]["handwriting_metadata"] = {
        "仓库接收人": {"value": "李明", "source": "OCR", "low_confidence": False}}
    updated = refresh_provider_result(current)
    assert CONFLICT in updated["review_reasons"]
    assert any(item["code"] == "provider_conflict" and item["scope"] == "handwriting"
               for item in updated["review_issues"])
    assert updated["fields"]["仓库接收人"] in {"李明", "郭亮"}
    assert updated["final_result"] == "需人工复核"
    assert refresh_provider_result(updated) == updated


@pytest.mark.parametrize("change", [
    {"field_metadata": {"仓库接收人": {"source": "人工复核", "value": "李明"}}},
    {"signature_check": {"source": "人工复核", "human_confirmed_match": False}},
    {"signature_check": {"human_confirmed_match": True}},
    {"review_status": "确认通过"},
    {"human_note": "已核对签名"},
])
@pytest.mark.parametrize("refresh", [refresh_handwriting_result, refresh_provider_result])
def test_saved_selection_preserves_human_edits_and_signature_confirmation(change, refresh):
    current = saved_handwriting_result()
    current["fields"]["仓库接收人"] = "李明"
    current.update(change)
    assert refresh(current) == current


def test_missing_saved_handwriting_evidence_leaves_receiver_untouched():
    current = saved_handwriting_result()
    current["recognition_variants"] = {}
    current["fields"]["仓库接收人"] = "李明"
    assert refresh_provider_result(current) == current


@pytest.mark.parametrize("mode, status", [("none", "未核对"), ("any", "匹配"), ("all", "需人工复核")])
@pytest.mark.parametrize("refresh", [refresh_handwriting_result, refresh_provider_result])
def test_saved_selection_preserves_signature_match_mode_and_is_idempotent(mode, status, refresh):
    current = saved_handwriting_result()
    current["recognition_config"] = {"acceptance": {"signature_match_mode": mode}}
    updated = refresh(current)
    assert updated["signature_check"]["status"] == status
    assert updated["signature_check"]["match_mode"] == mode
    assert refresh(updated) == updated


def test_handwriting_only_repair_preserves_unrelated_structured_issues_and_provider_checks():
    current = saved_handwriting_result()
    unrelated = {"code": "missing_requirement", "scope": "date", "message": "缺少要求到货日期",
                 "blocking": True, "provider": "danzhengtong"}
    current["review_issues"].insert(0, deepcopy(unrelated))
    current["review_reasons"].insert(0, unrelated["message"])
    current["date_check"] = {"status": "匹配", "reliable": False, "backend": "单证通"}
    current["danzhengtong"] = {"mode": "real", "simulated": False, "status": "completed"}
    updated = refresh_handwriting_result(current)
    assert updated["review_issues"] == [unrelated]
    assert updated["review_reasons"] == [unrelated["message"]]
    assert updated["date_check"] == current["date_check"]
    assert updated["seal_check"] == current["seal_check"]
    assert updated["danzhengtong"] == current["danzhengtong"]


@pytest.mark.parametrize("invalid", ["failed", "pending", "simulated"])
def test_unusable_saved_candidates_cannot_erase_existing_receiver(invalid):
    current = saved_handwriting_result()
    current["fields"]["仓库接收人"] = "李明"
    variant = current["recognition_variants"]["handwriting"][1]
    if invalid == "failed":
        variant["error"] = "识别失败"
    elif invalid == "pending":
        variant["details"]["handwriting_metadata"]["仓库接收人"]["pending"] = True
    else:
        variant["details"]["danzhengtong"].update(mode="mock", simulated=True)
    updated = refresh_handwriting_result(current)
    assert updated["fields"]["仓库接收人"] == "李明"
    assert updated["recognition_variants"] == current["recognition_variants"]


def test_saved_selection_preview_and_audited_apply_keep_original_and_variants(tmp_path):
    path = tmp_path / "results.db"
    database = Database(path)
    database.initialize()
    current = saved_handwriting_result()
    result_id = database.insert_result(filename=current["filename"], stored_name="saved.jpg",
        preview_name="saved.jpg", task_id="", result=current)
    with database.connect() as connection:
        original = connection.execute("SELECT original_result_json FROM results WHERE id=?", (result_id,)).fetchone()[0]
    assert refresh_database(path, ids=[result_id])["counts"] == {"changed": 1}
    assert database.get_result(result_id)["fields"]["仓库接收人"] == ""
    assert database.history(result_id) == []
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"changed": 1}
    repaired = database.get_result(result_id)
    assert repaired["fields"]["仓库接收人"] == "郭亮"
    assert repaired["recognition_variants"] == current["recognition_variants"]
    history = database.history(result_id)
    assert len(history) == 1 and history[0]["action"] == REFRESH_ACTION
    assert json.loads(history[0]["before_json"])["fields"]["仓库接收人"] == ""
    with database.connect() as connection:
        assert connection.execute("SELECT original_result_json FROM results WHERE id=?", (result_id,)).fetchone()[0] == original
    assert refresh_database(path, ids=[result_id], apply=True)["counts"] == {"unchanged": 1}
