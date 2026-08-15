from tools.analyze_date_missing_month_consensus import select_saved_candidate
from tests.test_analyzer import _same_geometry_missing_month_artifacts


def test_select_saved_candidate_does_not_need_truth():
    result = {
        "date_check": {"required": "2025-05-11", "reliable": False},
        "processing_artifacts": {
            "date": _same_geometry_missing_month_artifacts()
        },
    }

    selected = select_saved_candidate(result)

    assert selected["selected"] is True
    assert selected["candidate"] == "2025-05-11"
    assert selected["production_confirmed"] is False


def test_select_saved_candidate_reports_production_marker():
    artifacts = _same_geometry_missing_month_artifacts()
    artifacts[0]["date_missing_month_consensus_candidate"] = "2025-05-11"
    result = {
        "date_check": {"required": "2025-05-11"},
        "processing_artifacts": {"date": artifacts},
    }

    assert select_saved_candidate(result)["production_confirmed"] is True
