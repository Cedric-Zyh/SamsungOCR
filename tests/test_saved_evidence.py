"""Saved source selection is shared by repair, replay and domain selectors."""

from copy import deepcopy

import pytest

from receipt_ocr.domain.results import StageOutcome
from receipt_ocr.domain.saved_evidence import (SavedStageEvidence, completed_real_trace,
                                              provider_trace, variant_details)
from receipt_ocr.domain.printed_field_selection import select_printed_fields
from receipt_ocr.domain.handwriting_selection import select_handwriting


def completed(**extra):
    return {"mode": "real", "simulated": False, "status": "completed", **extra}


@pytest.mark.parametrize("container", ["details", "result", "outcome"])
def test_payload_formats_retain_the_same_raw_objects(container):
    payload = {"seal_check": {"recognized": "测试客户收货章", "status": "匹配"},
               "danzhengtong_seal": completed(provider_extra={"unknown": [1, None]})}
    variant = (StageOutcome.completed("seal", "danzhengtong", payload) if container == "outcome"
               else {"method": "danzhengtong", container: payload})
    evidence = SavedStageEvidence.from_variant("seal", variant)
    assert variant_details(variant) is payload
    assert evidence.check is payload["seal_check"]
    assert evidence.trace is payload["danzhengtong_seal"]
    assert evidence.real_provider and evidence.active


@pytest.mark.parametrize("trace", [{}, completed(status="failed"),
                                   completed(simulated=True), completed(error="stale error")])
def test_explicit_independent_trace_cannot_borrow_completed_page_trace(trace):
    page = completed(marker="page")
    document = {"danzhengtong": page, "danzhengtong_seal": completed(marker="global seal")}
    details = {"danzhengtong": page, "danzhengtong_seal": trace, "seal_check": {"status": "不匹配"}}
    evidence = SavedStageEvidence.from_variant("seal", {"method": "danzhengtong", "details": details}, document)
    assert evidence.trace == (trace or {})
    assert not evidence.real_provider
    assert provider_trace(document, "date", details) is page


def test_old_page_only_data_and_missing_independent_trace_are_distinguished():
    page = completed()
    assert provider_trace({"danzhengtong": page}, "seal") is page
    details = {"seal_check": {"model_id": "independent-test", "candidates": []}}
    assert provider_trace({"danzhengtong": page}, "seal", details) == {}
    assert provider_trace({"danzhengtong": page}, "date", details) is page


def test_public_variant_null_seal_placeholder_does_not_invent_an_independent_request():
    page = completed(marker="old page-only result")
    details = {"danzhengtong": page, "danzhengtong_seal": None}
    assert provider_trace({}, "seal", details) is page
    failed = completed(status="failed")
    assert provider_trace({"danzhengtong_seal": failed}, "seal", details) is failed
    assert provider_trace({"danzhengtong_seal": None}, "seal", details) == {}
    details["seal_check"] = {"model_id": "independent-test", "candidates": []}
    assert provider_trace({}, "seal", details) == {}


@pytest.mark.parametrize("placeholder", [{}, None])
@pytest.mark.parametrize("stage", ["date", "seal", "fields"])
def test_empty_legacy_page_placeholder_still_uses_document_page_trace(placeholder, stage):
    page = completed(marker="document page")
    assert provider_trace({"danzhengtong": page}, stage, {"danzhengtong": placeholder}) is page
    failed = completed(status="failed")
    assert provider_trace({"danzhengtong": page}, stage, {"danzhengtong": failed}) is failed


@pytest.mark.parametrize("marker", [{"pending": True}, {"error": "failed"}, {"simulated": True},
                                    {"execution_status": "skipped"}, {"status": "paused"}])
@pytest.mark.parametrize("location", ["variant", "payload", "check", "trace"])
def test_execution_markers_block_evidence_at_each_level(marker, location):
    payload = {"date_check": {"actual": "2026-11-10", "status": "匹配"}, "danzhengtong": completed()}
    variant = {"method": "danzhengtong", "result": payload}
    target = {"variant": variant, "payload": payload, "check": payload["date_check"],
              "trace": payload["danzhengtong"]}[location]
    target.update(marker)
    outcome = StageOutcome.from_variant("date", variant)
    assert not SavedStageEvidence.from_variant("date", outcome).active
    assert not SavedStageEvidence.from_variant("date", outcome).real_provider


@pytest.mark.parametrize("stage,select,fields_key,metadata_key,name", [
    ("fields", select_printed_fields, "fields", "field_metadata", "客户名称"),
    ("handwriting", select_handwriting, "handwriting_fields", "handwriting_metadata", "仓库接收人"),
])
def test_field_selectors_read_typed_outcomes_and_keep_top_level_gates(stage, select, fields_key, metadata_key, name):
    valid = {"method": "paddle_v6", "result": {fields_key: {name: "有效读数"},
             metadata_key: {name: {"confidence": .95}}}}
    inactive = deepcopy(valid)
    inactive.update(method="danzhengtong", pending=True)
    inactive["result"][fields_key][name] = "旧读数"
    result = select([StageOutcome.from_variant(stage, inactive), StageOutcome.from_variant(stage, valid)])
    assert result["fields"][name] == "有效读数"


def test_real_completion_rejects_error_even_if_old_status_says_completed():
    assert completed_real_trace(completed())
    assert not completed_real_trace(completed(error="query failed"))
