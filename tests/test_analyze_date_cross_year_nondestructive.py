import json

from tests.test_analyzer import _cross_year_nondestructive_artifacts
from tools.analyze_date_cross_year_nondestructive import select_saved_candidate


def _result(*, reliable: bool = False, confirmed: bool = False) -> dict:
    artifacts = _cross_year_nondestructive_artifacts()
    if confirmed:
        artifacts[0]["date_cross_year_nondestructive_candidate"] = (
            "2026-05-24"
        )
    return {
        "date_check": {
            "required": "2025-05-24",
            "reliable": reliable,
        },
        "processing_artifacts": {"date": artifacts},
    }


def test_saved_cross_year_audit_distinguishes_prefilter_and_production():
    selected = select_saved_candidate(_result())
    assert selected["selected"] is True
    assert selected["candidate"] == "2026-05-24"
    assert selected["confirmed"] is False
    json.dumps(selected, ensure_ascii=False)

    confirmed = select_saved_candidate(_result(confirmed=True))
    assert confirmed["selected"] is True
    assert confirmed["confirmed"] is True


def test_saved_cross_year_audit_skips_existing_reliable_route():
    assert select_saved_candidate(_result(reliable=True)) == {
        "selected": False,
        "reason": "当前日期已由既有路线可靠确认",
    }
