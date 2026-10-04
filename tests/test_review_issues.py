"""Acceptance rules depend on typed evidence, not display wording."""
import pytest
from receipt_ocr.domain.issues import issue
from receipt_ocr.domain.decision import decide_overall, finalize_result, has_provider_failure


@pytest.mark.parametrize("message", ["收货日期无法可靠判断", "印章信息已更新", "Evidence needs attention"])
def test_disabled_scope_ignores_only_its_own_issues(message):
    check = {"status": "匹配", "reliable": True}
    acceptance = {"date_match_mode": "none"}
    assert decide_overall(check, check, [], acceptance,
                          review_issues=[issue("evidence_unreliable", "date", message)]) == "通过"
    assert decide_overall(check, check, [], acceptance,
                          review_issues=[issue("review_required", "document", message)]) == "需人工复核"


def test_low_confidence_policy_uses_code_instead_of_words():
    check = {"status": "匹配", "reliable": True}
    acceptance = {"low_confidence_mode": "ignore"}
    assert decide_overall(check, check, [], acceptance,
                          review_issues=[issue("low_confidence", "fields", "Uncertain field")]) == "通过"
    assert decide_overall(check, check, [], acceptance,
                          review_issues=[issue("provider_conflict", "fields", "低置信度")]) == "需人工复核"


def test_provider_identity_survives_localized_error_text():
    record = {"review_issues": [issue("provider_failure", "fields", "Service unavailable", provider="danzhengtong")]}
    assert finalize_result(record)["overall"] == "识别失败"
    assert not has_provider_failure({"review_reasons": ["单证通失败"]})


def test_historical_reason_migration_is_one_way_and_preserves_machine_snapshot(tmp_path):
    import json
    from receipt_ocr.persistence.database import Database
    db = Database(tmp_path / "records.db")
    db.initialize()
    record = {"overall": "需人工复核", "review_reasons": ["单证通等待超时"]}
    identity = db.insert_result(filename="old.jpg", stored_name="x", preview_name="", task_id="", result=record)
    db.initialize()
    assert has_provider_failure(db.get_result(identity))
    with db.connect() as connection:
        original = json.loads(connection.execute("SELECT original_result_json FROM results").fetchone()[0])
        before = connection.execute("SELECT result_json FROM results").fetchone()[0]
    assert "review_issues" not in original
    db.initialize()
    with db.connect() as connection:
        assert connection.execute("SELECT result_json FROM results").fetchone()[0] == before
