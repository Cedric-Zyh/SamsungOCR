from pathlib import Path

import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.date.contracts import (
    DateInputKind,
    DateInputRole,
    DateOcrResult,
    PreparedDate,
    PreparedDateInput,
)
from receipt_ocr.recognition.date.ocr.interface import DateOcrReader
from receipt_ocr.recognition.date.ocr.regions import _recognize_region_variants
from receipt_ocr.recognition.date.contracts import DateCropRegion, DateCropRun, DateCropServices
from receipt_ocr.recognition.date.postprocess.decision import _select_complete_dates
from receipt_ocr.recognition.date.postprocess.state import DateDecision, DateConsensus, DateStageEvidence


def test_original_date_input_is_display_only():
    original = PreparedDateInput(
        "date:original",
        "date-tight",
        Path("/tmp/date-original.jpg"),
        DateInputKind.ORIGINAL,
        DateInputRole.DISPLAY,
        "日期行原图",
        "line",
    )
    clean = PreparedDateInput(
        "date:clean",
        "date-tight",
        Path("/tmp/date-clean.png"),
        DateInputKind.COLOR_CLEAN,
        DateInputRole.DECISION,
        "日期行去印章色",
        "line",
    )
    prepared = PreparedDate("date-tight", (original, clean))
    assert prepared.for_ocr(coordinate_space="line") == (clean,)
    assert original.ocr_input is False
    assert clean.ocr_input is True


def test_date_ocr_reader_rejects_original_before_provider_call(monkeypatch):
    original = PreparedDateInput(
        "date:original",
        "date-tight",
        Path("/tmp/date-original.jpg"),
        DateInputKind.ORIGINAL,
        DateInputRole.DISPLAY,
        "日期行原图",
        "line",
    )
    called = []

    def provider(*args, **kwargs):
        called.append((args, kwargs))
        return [TextObservation("2025年8月11日", 0.99, 0, 0, 1, 1)]

    with pytest.raises(ValueError, match="不参与识别"):
        DateOcrReader().read(original, provider="paddle", recognize_text=provider)
    assert called == []


def test_required_date_is_not_used_to_complete_a_partial_ocr_read():
    evidence = DateStageEvidence(
        fields={"要求到货": "2026-02-12"},
        combined_rows=[TextObservation("202年2月2日", 0.95, 0.8, 0.55, 0.1, 0.04)],
        date_rows=[],
        date_artifacts=[],
        has_receipt_footer=True,
        anchor_y=0.47,
    )
    decision = DateDecision()
    _select_complete_dates(evidence, decision, DateConsensus())
    assert decision.actual_date is None


@pytest.mark.parametrize("required, observations, expected", [
    ("2025-08-31", [("2025年8月3日", .9794), ("2025年8月31日", .958)], "2025-08-31"),
    # Matching is a priority, not a small score bonus.
    ("2025-08-31", [("2025年8月3日", .99), ("2025年8月31日", .4)], "2025-08-31"),
    ("2025-08-30", [("2025年8月3日", .9794), ("2025年8月31日", .958)], "2025-08-03"),
    ("", [("2025年8月3日", .8), ("2025年8月31日", .9)], "2025-08-31"),
    ("未识别", [("2025年8月3日", .8), ("2025年8月31日", .9)], "2025-08-31"),
    # Repeated lower-confidence transforms must not outweigh confidence.
    ("2025-08-30", [("2025年8月3日", .8)] * 4 + [("2025年8月31日", .9)], "2025-08-31"),
    # A partial apparent match cannot borrow the business year/month.
    ("2025-08-31", [("202年8月31日", .99), ("2025年8月3日", .8)], "2025-08-03"),
])
def test_date_selection_prefers_required_match_then_confidence(required, observations, expected):
    rows = [TextObservation(text, score, .8, .55, .1, .04)
            for text, score in observations]
    evidence = DateStageEvidence(
        fields={"要求到货": required}, combined_rows=rows, date_rows=rows,
        date_artifacts=[], has_receipt_footer=True, anchor_y=.47,
    )
    decision = DateDecision()
    _select_complete_dates(evidence, decision, DateConsensus())
    assert decision.actual_date.isoformat() == expected


def test_required_match_must_be_in_an_eligible_date_region():
    valid = TextObservation("2025年8月3日", .8, .8, .55, .1, .04)
    outside = TextObservation("2025年8月31日", .99, .1, .1, .1, .04)
    evidence = DateStageEvidence(
        fields={"要求到货": "2025-08-31"}, combined_rows=[outside, valid],
        date_rows=[], date_artifacts=[], has_receipt_footer=True, anchor_y=.47,
    )
    decision = DateDecision()
    _select_complete_dates(evidence, decision, DateConsensus())
    assert decision.date_row is valid


def test_region_ocr_never_routes_to_the_retired_secondary_backend(tmp_path):
    calls = []

    def recognize(path, *, backend, **_options):
        calls.append((Path(path).name, backend))
        return [TextObservation("2025年8月11日", 0.9, 0.8, 0.55, 0.1, 0.04)]

    region = DateCropRegion("tight", True, 0.0, False)
    region.images.raw = tmp_path / "original.jpg"
    region.images.color_clean = tmp_path / "color.png"
    region.images.crop = tmp_path / "table.png"
    run = DateCropRun(
        source=tmp_path / "source.jpg",
        anchor_y=0.47,
        required_text="2025-08-11",
        artifact_dir=None,
        artifact_url_prefix="",
        ocr_backend="vision",
        allow_strict_date_without_requirement=False,
        creation_text="",
        temp_dir=tmp_path,
        services=DateCropServices(
            save_receipt_date_crop=lambda *a, **k: None,
            save_date_line_crop=lambda *a, **k: None,
            recognize_text=recognize,
            backend_label=lambda value: value,
        ),
    )
    _recognize_region_variants(run, region)
    assert calls
    assert {backend for _, backend in calls} == {"vision"}
    assert all("original" not in name for name, _ in calls)
