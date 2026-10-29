from copy import deepcopy
from types import SimpleNamespace

import pytest

from receipt_ocr.application.assembly import assemble_result
from receipt_ocr.domain.handwriting_selection import (
    canonical_handwriting_fields, select_handwriting,
)
from receipt_ocr.domain.stages import STAGES


def variant(method, value, *, confidence=None, low_confidence=None, **metadata):
    meta = {"value": value, "original": value, "source": method, **metadata}
    if confidence is not None:
        meta["confidence"] = confidence
    if low_confidence is not None:
        meta["low_confidence"] = low_confidence
    return {"method": method, "result": {
        "handwriting_fields": {"仓库接收人": value},
        "handwriting_metadata": {"仓库接收人": meta},
    }}


def saved_variant(live):
    return {"method": live["method"], "error": live.get("error"),
            "value": deepcopy(live["result"]["handwriting_fields"]),
            "details": deepcopy(live["result"])}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("saved", [False, True])
def test_real_nonempty_result_wins_over_empty_and_keeps_provenance(reverse, saved):
    variants = [variant("paddle_v6", ""),
                variant("danzhengtong", "郭亮", low_confidence=False,
                        source="单证通", provider_ratio=1, simulated=False, position=[])]
    if reverse:
        variants.reverse()
    if saved:
        variants = [saved_variant(item) for item in variants]
    before = deepcopy(variants)
    selected = select_handwriting(variants)
    assert selected["fields"] == {"仓库接收人": "郭亮"}
    assert selected["selected_method"] == "danzhengtong"
    assert selected["metadata"]["仓库接收人"]["source"] == "单证通"
    assert "confidence" not in selected["metadata"]["仓库接收人"]
    assert not selected["conflict"]
    selected["metadata"]["仓库接收人"]["position"].append(10)
    assert variants == before


def test_same_name_ignores_extra_fields_missing_empty_keys_and_whitespace():
    local = variant("paddle_v6", "郭 亮", confidence=.5, low_confidence=True)
    remote = variant("danzhengtong", "郭亮", source="单证通", low_confidence=False)
    remote["result"]["handwriting_fields"].update(实收数量="10", 拒收数量="")
    assert canonical_handwriting_fields(local["result"]) == canonical_handwriting_fields(remote["result"])
    selected = select_handwriting([local, remote])
    assert selected["fields"] == {"仓库接收人": "郭亮"}
    assert selected["selected_method"] == "danzhengtong"
    assert not selected["conflict"]
    assert canonical_handwriting_fields({}) == canonical_handwriting_fields({"handwriting_fields": {"仓库接收人": None}})
    assert select_handwriting([variant("local", ""), variant("remote", " ")])["selected_method"] is None


@pytest.mark.parametrize("strong_method,weak_method", [
    ("danzhengtong", "paddle_v6"), ("paddle_v6", "danzhengtong"),
])
def test_explicit_low_confidence_guess_cannot_override_clearer_result(strong_method, weak_method):
    weak = variant(weak_method, "郭豪", confidence=.5, low_confidence=True)
    strong = variant(strong_method, "郭亮", low_confidence=False)
    selected = select_handwriting([weak, strong])
    assert selected["selected_method"] == strong_method
    assert selected["fields"]["仓库接收人"] == "郭亮"
    assert not selected["conflict"]
    assert weak["result"]["handwriting_metadata"]["仓库接收人"]["low_confidence"] is True


@pytest.mark.parametrize("first,second", [(.90, .94), (.64, .65), (.84, .85)])
def test_close_nonempty_results_keep_ambiguity_and_choose_stronger_candidate(first, second):
    selected = select_handwriting([
        variant("first", "郭豪", confidence=first),
        variant("second", "郭亮", confidence=second),
    ])
    assert selected["selected_method"] == "second"
    assert selected["conflict"]
    assert "仍需核对原图" in selected["reason"]


def test_substantial_valid_confidence_difference_can_select_a_candidate():
    selected = select_handwriting([
        variant("first", "郭豪", confidence=.70),
        variant("second", "郭亮", confidence=.96),
    ])
    assert selected["fields"]["仓库接收人"] == "郭亮"
    assert not selected["conflict"]


@pytest.mark.parametrize("bad_confidence", [True, float("nan"), float("inf"), 95, -1, "95%"])
def test_invalid_confidence_cannot_silently_resolve_different_names(bad_confidence):
    selected = select_handwriting([
        variant("first", "郭豪", confidence=bad_confidence),
        variant("second", "郭亮", confidence=.91),
    ])
    assert selected["selected_method"] == "second"
    assert selected["conflict"]


def test_provider_ratio_does_not_count_as_a_confidence_score():
    selected = select_handwriting([
        variant("first", "郭豪", low_confidence=False, provider_ratio=1),
        variant("second", "郭亮", low_confidence=False, provider_ratio=.2),
    ])
    assert selected["conflict"]
    assert "confidence" not in selected["metadata"]["仓库接收人"]


@pytest.mark.parametrize("unusable", ["error", "failed", "skipped", "pending", "simulated", "mock_trace", "failed_trace"])
@pytest.mark.parametrize("saved", [False, True])
def test_unusable_candidate_cannot_overrule_real_recognition(unusable, saved):
    invalid = variant("invalid", "郭豪", confidence=.99, low_confidence=False)
    if unusable == "error":
        invalid["error"] = "识别失败"
    elif unusable in {"failed", "skipped"}:
        invalid["result"]["execution_status"] = unusable
    elif unusable in {"pending", "simulated"}:
        invalid["result"]["handwriting_metadata"]["仓库接收人"][unusable] = True
    elif unusable == "mock_trace":
        invalid["result"]["danzhengtong"] = {"mode": "mock"}
    else:
        invalid["result"]["danzhengtong"] = {"mode": "real", "status": "failed"}
    real = variant("real", "郭亮", confidence=.5, low_confidence=True)
    variants = [invalid, real]
    if saved:
        variants = [saved_variant(item) for item in variants]
    selected = select_handwriting(variants)
    assert selected["fields"]["仓库接收人"] == "郭亮"
    assert selected["selected_method"] == "real"
    assert not selected["conflict"]
    assert select_handwriting(variants[:1])["selected_method"] is None


def test_assembly_uses_selected_receiver_for_signature_check_and_preserves_raw_variants():
    document_type = {"type": "receipt", "reliable": True, "reasons": []}
    context = SimpleNamespace(
        evidence=lambda: {"document_type": deepcopy(document_type)},
        routing_issues=[], routing_reasons=[], document_type=document_type,
        primary_backend=None, has_footer=True,
    )
    config = {stage: [] for stage in STAGES}
    config["handwriting"] = ["paddle_v6", "danzhengtong"]
    results = {stage: [] for stage in STAGES}
    local = variant("paddle_v6", "")
    remote = variant("danzhengtong", "郭亮", low_confidence=False, source="单证通")
    remote["result"]["handwriting_fields"].update(实收数量="999", 拒收数量="999")
    results["handwriting"] = [local, remote]
    previous = {"仓库联系人": "郭亮", "仓库接收人": "", "实收数量": "10"}
    before, previous_before = deepcopy(results), deepcopy(previous)

    output = assemble_result(context, config, results, [], {}, previous, None, None, {})

    assert output["fields"]["仓库接收人"] == "郭亮"
    assert output["fields"]["实收数量"] == "10"
    assert output["signature_check"]["receiver"] == "郭亮"
    assert output["signature_check"]["status"] == "匹配"
    assert output["field_metadata"]["仓库接收人"]["source"] == "单证通"
    assert output["handwriting_selection"]["selected_method"] == "danzhengtong"
    assert not any(item["code"] == "provider_conflict" for item in output["review_issues"])
    assert output["recognition_variants"]["handwriting"][1]["details"]["handwriting_fields"] == remote["result"]["handwriting_fields"]
    assert results == before and previous == previous_before


def test_selection_never_prefers_the_name_that_matches_printed_contact():
    weak = variant("first", "郭亮", confidence=.5, low_confidence=True)
    strong = variant("second", "郭豪", confidence=.95, low_confidence=False)
    for item in (weak, strong):
        item["result"]["fields"] = {"仓库联系人": "郭亮"}
    selected = select_handwriting([weak, strong])
    assert selected["fields"]["仓库接收人"] == "郭豪"
