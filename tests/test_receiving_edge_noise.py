"""Tiny detached boundary detections must not become stamp identity text."""

from copy import deepcopy

import pytest

from receipt_ocr.recognition.seal.postprocess.receiving_identity import decide_receiving_identity
from receipt_ocr.recognition.seal.postprocess.receiving_policy import (
    map_receiving_detections, older_identity_observations, receiving_detection_evidence,
)


TITLE = "上海金山新日仓021DCO"
PURPOSE = "收货专用章①"


def box(text, bounds, confidence=.99):
    left, top, right, bottom = bounds
    # The canonical OCR input adds 32 pixels around the physical frame.
    return {"text": text, "confidence": confidence,
            "points": [[left+32, top+32], [right+32, top+32],
                       [right+32, bottom+32], [left+32, bottom+32]]}


def artifact(extra):
    return {
        "rectangular_frame": {"crop_box": [0, 0, 960, 400], "canonical_width": 960, "padding": 32,
                              "detected_boxes": [box(TITLE, [60, 30, 880, 110]), extra,
                                                 box(PURPOSE, [80, 160, 800, 260])]},
        "rectangular_fixed_rows": [
            {"crop_box": [0, 0, 960, 140], "coordinate_space": "oriented"},
            {"crop_box": [0, 140, 960, 280], "coordinate_space": "oriented"},
            {"crop_box": [0, 280, 960, 400], "coordinate_space": "oriented"},
        ],
    }


@pytest.mark.parametrize("text", ["II", "8", "干"])
@pytest.mark.parametrize("bounds,edge", [([100, 0, 109, 10], "top"), ([100, 130, 109, 140], "bottom")])
def test_small_detached_edge_box_is_excluded_by_geometry_and_audited(text, bounds, edge):
    current = artifact(box(text, bounds))
    before = deepcopy(current)
    evidence = receiving_detection_evidence(current)
    assert [item["text"] for item in evidence["mapped"]] == [TITLE, PURPOSE]
    excluded = evidence["excluded"]
    assert len(excluded) == 1 and excluded[0]["detection_index"] == 1
    assert excluded[0]["text"] == text and excluded[0]["edge"] == edge
    assert excluded[0]["points"] == current["rectangular_frame"]["detected_boxes"][1]["points"]
    assert excluded[0]["reason"] == "small_off_baseline_edge_detection"
    assert map_receiving_detections(current) == evidence["mapped"]
    assert current == before


@pytest.mark.parametrize("bounds", [
    [100, 35, 109, 45],    # A small symbol within the actual text band.
    [100, 15, 109, 25],    # Detached, but not sufficiently close to the cell edge.
    [100, 2, 140, 40],    # Ordinary-sized text close to the top border.
    [100, 0, 180, 10],    # A small-height but wide textual token.
    [850, 190, 864, 204],  # A small stamp number beside the purpose text.
])
def test_small_inline_numbers_and_real_edge_text_remain_evidence(bounds):
    current = artifact(box("②", bounds))
    evidence = receiving_detection_evidence(current)
    assert not evidence["excluded"]
    assert any(item["text"] == "②" for item in evidence["mapped"])


@pytest.mark.parametrize("change", ["no_support", "different_row", "unreliable_support", "narrow_support"])
def test_small_box_requires_substantial_reliable_text_support_in_the_same_cell(change):
    current = artifact(box("II", [100, 0, 109, 10]))
    boxes = current["rectangular_frame"]["detected_boxes"]
    if change == "no_support":
        boxes[:] = [boxes[1]]
    elif change == "different_row":
        boxes.pop(0)
    elif change == "unreliable_support":
        boxes[0]["confidence"] = .4
    else:
        boxes[0] = box("I", [60, 30, 75, 110])
    evidence = receiving_detection_evidence(current)
    assert not evidence["excluded"]
    assert any(item["text"] == "II" for item in evidence["mapped"])


def test_filtered_edge_mark_cannot_reenter_through_older_frame_observations():
    current = artifact(box("II", [100, 0, 109, 10]))
    current["orientation"] = {"detected_boxes": [
        box("独立方向中的II", [60, 30, 880, 110]), box(PURPOSE, [80, 160, 800, 260])
    ]}
    before = deepcopy(current)
    observed = [item["text"] for item in older_identity_observations(current)]
    assert "II" not in observed
    assert TITLE in observed and PURPOSE in observed
    assert "独立方向中的II" in observed
    assert current == before


def test_edge_noise_does_not_block_complete_observed_identity_but_inline_disagreement_does():
    rows = [{"row_index": 0, "text": TITLE, "confidence": .99},
            {"row_index": 1, "text": PURPOSE, "confidence": .99},
            {"row_index": 2, "text": "日期：收货人：", "confidence": .99}]
    noise = artifact(box("II", [100, 0, 109, 10]))
    decision = decide_receiving_identity(rows, detected_boxes=map_receiving_detections(noise), grid_verified=True)
    assert decision["can_compare"] and decision["identity_text"] == TITLE + PURPOSE

    actual_text = artifact(box("II", [890, 50, 900, 60]))
    conflict = decide_receiving_identity(rows, detected_boxes=map_receiving_detections(actual_text), grid_verified=True)
    assert not conflict["can_compare"]
    assert conflict["conflicts"][0]["reason"] == "disagreeing_whole_row_observations"
