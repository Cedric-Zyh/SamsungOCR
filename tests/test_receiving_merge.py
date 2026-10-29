"""Previously read physical rows survive later rereads that omit their number."""
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal import receiving
from receipt_ocr.recognition.seal.postprocess.receiving_identity import decide_receiving_identity
from receipt_ocr.recognition.seal.postprocess.receiving_merge import (
    complete_receiving_candidate_number, original_physical_row_observations,
)


TITLE = "北京永乐DC010DCM"
BARE = "收货专用章"
NUMBERED = BARE + "②"


def measured_stamp():
    stamp = {"index": 0, "region_id": "seal-0", "color": "red", "shape_code": "rectangle",
             "rectangular_layout": "frame_rows", "rectangular_fixed_rows": [],
             "rectangular_row_urls": [], "original_url": "/files/test/seal-0-original.png",
             "inputs": [], "reads": [], "errors": []}
    for index, text in enumerate([TITLE, NUMBERED, "收货人", "日期"]):
        url = f"/files/test/seal-0-rectangular-row-{index}.png"
        input_id = f"seal-0:rectangular-row-{index}"
        stamp["rectangular_fixed_rows"].append({
            "crop_box": [0, index * 80, 300, index * 80 + 60], "coordinate_space": "oriented"})
        stamp["rectangular_row_urls"].append(url)
        stamp["inputs"].append({"id": input_id, "channel": "body", "image_url": url,
                                "read_mode": "line", "status": "completed", "rectangle_row_index": index})
        stamp["reads"].append({"id": input_id + ":0", "input_id": input_id, "channel": "body",
                               "region_id": "seal-0", "image_url": url, "rectangle_row_index": index,
                               "provider": "paddle_v6", "text": text, "confidence": .943,
                               "matching": False})
    return stamp


def primary_decision(*, old_number=NUMBERED, new_number=BARE, title=TITLE):
    stamp = measured_stamp()
    stamp["reads"][1]["text"] = old_number
    old = original_physical_row_observations(stamp, provider="paddle_v6")
    rows = [{"row_index": index, "text": text, "confidence": 1., "variants": old[index]}
            for index, text in enumerate([title, new_number, "收货人", "日期"])]
    return decide_receiving_identity(rows, grid_verified=True)


def test_original_numbered_line_survives_higher_confidence_omission():
    stamp = measured_stamp()
    before = deepcopy(stamp)
    prior = original_physical_row_observations(stamp, provider="paddle_v6")
    assert prior[1][0]["text"] == NUMBERED
    assert prior[1][0]["source"] == "original_physical_row"
    assert prior[1][0]["read_ids"] == ["seal-0:rectangular-row-1:0"]
    assert stamp == before
    result = primary_decision()
    assert result["can_compare"] is True
    assert result["identity_text"] == TITLE + NUMBERED
    assert result["identity_rows"][1]["text"] == NUMBERED


@pytest.mark.parametrize("target,key,value", [
    ("read", "region_id", "seal-1"), ("read", "rectangle_row_index", 0),
    ("read", "image_url", "/files/other.png"), ("read", "provider", "qingtong"),
    ("read", "channel", "template"), ("read", "input_id", "seal-1:rectangular-row-1"),
    ("read", "confidence", .74), ("read", "confidence", True),
    ("read", "confidence", float("nan")), ("read", "simulated", True),
    ("read", "status", "failed"), ("read", "error", "unavailable"),
    ("input", "status", "failed"), ("input", "simulated", True),
    ("input", "rectangle_row_index", True), ("input", "read_mode", "page"),
    ("input", "image_url", "/files/other.png"), ("input", "channel", "requirement"),
    ("crop", "coordinate_space", "original"), ("crop", "crop_box", [0, 0, 0, 50]),
    ("crop", "crop_box", [0, 0, float("inf"), 50]),
])
def test_unbound_or_unreliable_original_read_cannot_supply_number(target, key, value):
    stamp = measured_stamp()
    fields = {"read": stamp["reads"][1], "input": stamp["inputs"][1],
              "crop": stamp["rectangular_fixed_rows"][1]}
    fields[target][key] = value
    assert 1 not in original_physical_row_observations(stamp, provider="paddle_v6")


def test_a_partial_failed_multi_segment_read_is_not_silently_reassembled():
    stamp = measured_stamp()
    stamp["reads"][1]["text"] = BARE
    stamp["reads"].append({**stamp["reads"][1], "id": "seal-0:rectangular-row-1:1", "text": "②"})
    assert original_physical_row_observations(stamp, provider="paddle_v6")[1][0]["text"] == NUMBERED
    stamp["reads"][-1]["confidence"] = .3
    assert 1 not in original_physical_row_observations(stamp, provider="paddle_v6")


@pytest.mark.parametrize("new", [BARE + "③", BARE + "2", BARE + "(2)", "验收专用章②"])
def test_differing_actual_number_or_purpose_still_conflicts(new):
    result = primary_decision(new_number=new)
    assert result["can_compare"] is False
    assert any(conflict["row_index"] == 1 for conflict in result["conflicts"])
    assert complete_receiving_candidate_number(TITLE + BARE, result) is None


def test_number_survives_independent_title_conflict_without_accepting_identity():
    result = primary_decision(title="北京永乐bC010DCM")
    assert result["identity_text"] == ""
    assert result["can_compare"] is False
    assert result["identity_rows"] == [{"row_index": 1, "text": NUMBERED, "observations": [
        {"text": BARE, "confidence": 1., "source": "primary"},
        {"text": NUMBERED, "confidence": .943, "source": "original_physical_row"}]}]
    before = deepcopy(result)
    completion = complete_receiving_candidate_number(TITLE + BARE, result)
    assert completion["text"] == TITLE + NUMBERED
    assert completion["number_text"] == "②"
    assert completion["policy"] == "same_physical_row_number_retained"
    assert result == before


@pytest.mark.parametrize("candidate", [TITLE + BARE + "③", TITLE + BARE + "2",
                                        TITLE + "收费专用章", TITLE, BARE + BARE])
def test_existing_number_or_unrelated_candidate_is_never_replaced(candidate):
    assert complete_receiving_candidate_number(candidate, primary_decision()) is None


def test_number_cannot_come_only_from_selected_text_without_actual_observation():
    decision = primary_decision()
    decision["identity_rows"][1]["observations"] = [{"text": BARE, "confidence": 1.}]
    assert complete_receiving_candidate_number(TITLE + BARE, decision) is None


@pytest.mark.parametrize("number", ["②", "(2)", "（２）"])
def test_candidate_preserves_original_number_glyph(number):
    decision = primary_decision(old_number=BARE + number, title="北京永乐bC010DCM")
    result = complete_receiving_candidate_number(TITLE + BARE, decision)
    assert result["text"] == TITLE + BARE + number


@pytest.mark.parametrize("conflicting_title", [False, True])
def test_live_hook_keeps_original_circle_after_actual_reread(monkeypatch, tmp_path, conflicting_title):
    artifact = measured_stamp()
    original_reads = deepcopy(artifact["reads"])
    for url in artifact["rectangular_row_urls"]:
        Image.new("RGB", (340, 100), "white").save(tmp_path / Path(url).name)
    Image.new("RGB", (340, 400), "white").save(tmp_path / "seal-0-original.png")
    seen = []
    class Reader:
        def read_line(self, path, **kwargs):
            index = int(Path(path).stem.rsplit("-", 1)[-1])
            seen.append(index)
            title = "北京永乐bC010DCM" if conflicting_title else TITLE
            return [TextObservation([title, BARE, "收货人", "日期"][index], 1., 0, 0, 1, 1)]
    monkeypatch.setattr(receiving, "prepare_receiving_grid", lambda *args: {"accepted": False})
    monkeypatch.setattr(receiving, "_read_split", lambda *args: {
        "accepted": True, "identity_text": TITLE + BARE, "matching_allowed": False})
    result = receiving.recognize_receiving_form(
        artifact, tmp_path, "/files/test", "paddle_v6", Reader(), orientation_mode="combined")
    form = artifact["receiving_form"]
    assert form["candidate_text"] == TITLE + NUMBERED
    assert artifact["reads"][:4] == original_reads
    assert seen == [0, 1, 2, 3]
    if conflicting_title:
        assert result == [] and artifact["matching_texts"] == []
        assert form["status"] == "conflict" and form["identity_text"] == ""
        assert form["candidate_number_merge"]["number_text"] == "②"
        assert form["primary"]["conflicts"][0]["row_index"] == 0
        assert form["primary"]["identity_rows"][0]["text"] == NUMBERED
    else:
        assert result == [TITLE + NUMBERED]
        assert form["status"] == "adopted"
        assert form["identity_text"] == TITLE + NUMBERED
