from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.parsing_dates import (
    compare_partial_date_components,
    extract_date_components,
)
from receipt_ocr.stage_date import _tight_decision_rows
from receipt_ocr.date_decision import DateDecision, DateConsensus, DateStageEvidence
from receipt_ocr.date_selection import _apply_business_and_review_guards


def test_partial_date_keeps_year_and_month_when_day_is_missing():
    rows = [TextObservation("2026年2月", 0.9, 0.8, 0.55, 0.1, 0.04)]
    components = extract_date_components(rows)
    assert components == {"year": 2026, "month": 2, "day": None}

    check = compare_partial_date_components("2026-02-02", components)
    assert check["actual"] == ""
    assert check["actual_display"] == "2026-02-__"
    assert check["status"] == "部分识别"
    assert "缺少：日" in check["message"]

    assert extract_date_components(
        [TextObservation("2026", 0.8, 0.8, 0.55, 0.1, 0.04)]
    )["year"] == 2026


def test_tight_rows_are_used_when_artifact_provenance_is_available():
    tight = TextObservation("2026年2月", 0.9, 0.8, 0.55, 0.1, 0.04)
    wide = TextObservation("2026年2月2日", 0.95, 0.8, 0.55, 0.1, 0.04)
    artifacts = [
        {"variant": "紧凑区域", "decision_rows": [tight.to_dict()]},
        {"variant": "宽区域", "decision_rows": [wide.to_dict()]},
    ]
    assert _tight_decision_rows([wide], artifacts) == [tight]
    assert _tight_decision_rows([wide], [{"variant": "紧凑区域", "decision_rows": []}]) == []


def test_capped_date_audit_candidate_is_kept_for_review_comparison():
    audit = TextObservation("2026年2月2日", 0.25, 0.8, 0.55, 0.1, 0.04)
    artifacts = [{"variant": "紧凑区域", "decision_rows": []}]

    assert _tight_decision_rows([audit], artifacts) == [audit]


def test_capped_date_audit_candidate_survives_business_order_guard():
    audit = TextObservation("2026年2月2日", 0.25, 0.8, 0.55, 0.1, 0.04)
    evidence = DateStageEvidence(
        fields={"要求到货": "2026-02-12", "制单日期": "2026-02-10"},
        combined_rows=[],
        date_rows=[audit],
        date_artifacts=[],
        has_receipt_footer=True,
    )
    decision = DateDecision()

    _apply_business_and_review_guards(evidence, decision, DateConsensus())

    assert decision.actual_date.isoformat() == "2026-02-02"
    assert decision.date_check["status"] == "不匹配"
    assert "保留为人工复核候选" in decision.date_check["message"]
