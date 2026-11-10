"""Live outcomes and saved documents share a lossless JSON handoff."""

from copy import deepcopy
import json

import pytest

from receipt_ocr.application.document_evidence import evaluate_document_evidence, selected_date_outcome
from receipt_ocr.application.snapshots import DocumentSnapshot
from receipt_ocr.domain.results import StageOutcome
from receipt_ocr.domain.stages import STAGES


@pytest.mark.parametrize("variant,state", [
    ({"method": "paddle_v6", "result": {"seal_check": {"status": "匹配"},
                                         "provider_raw": {"unknown": [1, {"value": None}]}}}, "completed"),
    ({"method": "qingtong", "error": "timeout", "request_trace": {"attempt": 2}}, "failed"),
    ({"method": "danzhengtong", "pending": True, "result": None, "error": None,
      "remote_payload": {"reqUuid": "saved-id"}}, "pending"),
    ({"method": "paddle_v6", "result": {"seal_check": {
        "execution_status": "skipped", "skip_reason": "missing_requirement"}}}, "skipped"),
])
def test_outcome_state_and_unknown_provider_fields_survive_json_round_trip(variant, state):
    outcome = StageOutcome.from_variant("seal", deepcopy(variant))
    assert outcome.state == state
    assert json.loads(json.dumps(outcome.to_variant())) == variant
    saved = {"config": {stage: [] for stage in STAGES}, "results": {"seal": [variant]},
             "source_hash": "hash", "context": {}, "evidence": {"provider_page": ["raw"]}}
    snapshot = DocumentSnapshot.from_json(saved)
    encoded = json.loads(json.dumps(snapshot.to_json()))
    assert encoded["results"]["seal"] == [variant]
    assert encoded["context"] == {} and encoded["evidence"] == saved["evidence"]
    assert encoded["source_hash"] == "hash"
    assert DocumentSnapshot.from_json(encoded).results["seal"][0].state == state


def test_constructed_pending_and_skipped_outcomes_encode_their_explicit_state():
    pending = StageOutcome("seal", "danzhengtong", "pending")
    assert pending.to_variant() == {"method": "danzhengtong", "pending": True}
    skipped = StageOutcome("seal", "paddle_v6", "skipped", payload={"seal_check": {}})
    assert skipped.to_variant()["result"]["seal_check"]["execution_status"] == "skipped"
    with pytest.raises(ValueError, match="requires result"):
        StageOutcome("seal", "paddle_v6", "completed")


def test_snapshot_restores_only_routing_and_saved_observations(tmp_path):
    saved = {"config": {}, "context": {
        "document_type": {"type": "receipt", "reliable": True}, "has_footer": False,
        "page": "must not replace a collaborator", "source": "/unrelated.png", "_pages": {"bad": []},
    }, "evidence": {"qr_text": "saved-qr", "ocr_observations": [{
        "text": "签章要求：客户章", "confidence": .99, "x": .05, "y": .47, "width": .5, "height": .02,
    }]}}
    before = deepcopy(saved)
    snapshot = DocumentSnapshot.from_json(saved)
    context = snapshot.restore_context(tmp_path / "never-opened.png", "original.png")
    assert callable(context.page) and context._pages == {}
    assert context.source == (tmp_path / "never-opened.png").resolve()
    assert context.has_footer and context.footer_anchor.text == "签章要求：客户章"
    assert context.qr() == "saved-qr"
    assert saved == before
    assert snapshot.to_json()["context"]["page"] == saved["context"]["page"]


def test_saved_recomparison_keeps_original_observations_and_can_preserve_provider_checks():
    results = {"fields": [{"method": "danzhengtong", "result": {"fields": {"要求到货": "2026-11-09"}}}],
               "date": [{"method": "paddle_v6", "result": {"date_check": {
                   "required": "2026-11-08", "actual": "2026-11-09", "status": "不匹配", "reliable": True,
                   "provider_raw": {"scores": [.98]},
               }, "date_ocr_texts": ["2026年11月9日"], "vendor_extension": {"rows": ["unchanged"]}}}]}
    before = deepcopy(results)
    updated, fields = evaluate_document_evidence(results, {}, {}, recompare=True)
    assert fields["要求到货"] == "2026-11-09"
    assert updated["date"][0].payload["date_check"]["status"] == "匹配"
    assert updated["date"][0].payload["date_check"]["provider_raw"] == {"scores": [.98]}
    assert updated["date"][0].payload["date_ocr_texts"] == ["2026年11月9日"]
    assert updated["date"][0].payload["vendor_extension"] == {"rows": ["unchanged"]}
    preserved, _ = evaluate_document_evidence(results, {}, {}, recompare=True, preserve_provider_checks=True)
    assert preserved["date"][0].payload["date_check"]["status"] == "不匹配"
    assert results == before


@pytest.mark.parametrize("inactive", [{"pending": True}, {"error": "stale failure"},
                                      {"simulated": True}, {"status": "paused"}])
def test_date_selection_keeps_inactive_provider_gates_after_typed_handoff(inactive, tmp_path):
    from receipt_ocr.application.context import DocumentContext
    from receipt_ocr.application.assembly import assemble_result

    stale = StageOutcome.from_variant("date", {"method": "danzhengtong", **inactive, "result": {
        "date_check": {"required": "2026-11-09", "actual": "2026-11-09", "status": "匹配", "reliable": True},
    }})
    current = StageOutcome.completed("date", "paddle_v6", {"date_check": {
        "required": "2026-11-09", "actual": "2026-11-08", "status": "不匹配", "reliable": True,
    }})
    assert selected_date_outcome([stale, current], required_date="2026-11-09") is current
    config = {stage: [] for stage in STAGES}
    config.update(date=["danzhengtong", "paddle_v6"])
    context = DocumentContext(tmp_path / "not-opened.png")
    context.document_type = {"type": "receipt", "reliable": True}
    context.has_footer = True
    output = assemble_result(context, config, {"date": [stale, current]}, [], {},
                             {"要求到货": "2026-11-09"}, None, None, {})
    assert output["date_check"]["actual"] == "2026-11-08"
    assert output["ocr_stage_backends"]["date"]["id"] == "paddle_v6"
