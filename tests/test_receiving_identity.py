"""Receiving-form identity checks focus on missing/conflicting evidence."""
import copy
import inspect

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.postprocess.receiving_identity import (
    choose_receiving_identity,
    decide_receiving_identity,
    map_receiving_detections,
    normalize_receiving_text,
    older_identity_observations,
    verified_grid_alternatives,
    verify_blank_row,
)


IDENTITY = "广州花东仓020DCH收货专用章①"


def observations(subject="广州花东仓020DCH", purpose="收货专用章①"):
    return [
        {"row_index": 0, "text": subject, "confidence": .99},
        {"row_index": 1, "text": purpose, "confidence": .99},
        {"row_index": 2, "text": "收货人：刘延", "confidence": .99},
        {"row_index": 3, "text": "旧期2026年2月1日", "confidence": .99},
    ]


def grid_record():
    rows = observations()
    for row in rows:
        row["variants"] = [{"source": source, "text": row["text"], "confidence": .99}
                           for source in ["color", "gray"]]
    decision = decide_receiving_identity(rows, grid_verified=True)
    return {"accepted": True, "directions": [
        {"direction": 0, "rows": rows, "decision": decision},
        {"direction": 180, "rows": [], "decision": decide_receiving_identity([], grid_verified=True)},
    ]}


def weak_primary():
    return {"can_compare": False, "purpose_row_index": 1,
            "conflicts": [{"reason": "unread_nonblank_or_unknown_row"}]}


def test_receiving_values_are_separate_without_expected_text():
    result = decide_receiving_identity(observations(), grid_verified=True)
    assert result["can_compare"]
    assert result["identity_text"] == IDENTITY
    assert [row["row_index"] for row in result["form_rows"]] == [2, 3]
    assert "requirement" not in inspect.signature(decide_receiving_identity).parameters
    assert "requirement" not in inspect.signature(choose_receiving_identity).parameters


def test_omitted_company_characters_are_never_supplied_by_requirement():
    result = decide_receiving_identity(observations("州花东仓020DCH"), grid_verified=True)
    assert result["identity_text"] == "州花东仓020DCH收货专用章①"
    assert compare_seal_text_strict(IDENTITY, [result["identity_text"]])["status"] == "部分匹配"


@pytest.mark.parametrize("other", [
    "广州另一仓020DCH", "广州花东仓030DCH", "广州花东仓O20DCH", "广州花东仓020bCH",
])
def test_reliable_name_and_code_disagreements_block(other):
    rows = observations()
    rows[0]["variants"] = [{"text": other, "confidence": .98, "source": "another_actual_read"}]
    result = decide_receiving_identity(rows, grid_verified=True)
    assert not result["can_compare"]
    assert result["conflicts"][0]["reason"] == "disagreeing_whole_row_observations"


@pytest.mark.parametrize("other", ["收货专用章②", "收货专用章1", "☑收货专用章①"])
def test_wrong_or_plain_number_and_extra_symbol_are_not_deleted(other):
    rows = observations()
    result = decide_receiving_identity(rows, detected_boxes=[
        {"row_index": 1, "text": other, "confidence": .99}], grid_verified=True)
    assert not result["can_compare"]


def test_complete_observed_purpose_can_supply_detector_omission_only():
    rows = observations()
    result = decide_receiving_identity(rows, detected_boxes=[
        {"row_index": 1, "text": "收货专用章", "confidence": .99}], grid_verified=True)
    assert result["can_compare"]
    assert result["identity_text"] == IDENTITY
    assert normalize_receiving_text("O0bD①1") == "O0bD①1"


def test_split_detector_fields_are_joined_only_in_their_mapped_physical_row():
    rows = observations()
    result = decide_receiving_identity(rows, detected_boxes=[
        {"row_index": 0, "text": "020DCH", "confidence": .99, "left": 600},
        {"row_index": 0, "text": "广州花东仓", "confidence": .99, "left": 60},
        {"text": "其他公司", "confidence": .99},
    ], grid_verified=True)
    assert result["can_compare"]
    assert "unmapped_detector_not_used" in result["warnings"]


@pytest.mark.parametrize("kind", ["no_grid", "no_subject", "no_purpose", "two_purposes", "duplicate", "unknown_row"])
def test_incomplete_or_unverified_structure_does_not_become_an_identity(kind):
    rows = observations()
    if kind == "no_subject": rows = rows[1:]
    if kind == "no_purpose": rows[1]["text"] = "收贷专用章①"
    if kind == "two_purposes": rows[2]["text"] = "收货专用章②"
    if kind == "duplicate": rows[1]["row_index"] = 0
    if kind == "unknown_row":
        for row in rows[1:]: row["row_index"] += 1
        rows.insert(1, {"row_index": 1, "text": "", "confidence": 0})
    result = decide_receiving_identity(rows, grid_verified=kind != "no_grid")
    assert not result["can_compare"]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -.2, 1.2, True, "bad"])
def test_invalid_confidence_cannot_supply_subject(score):
    rows = observations()
    rows[0]["confidence"] = score
    assert not decide_receiving_identity(rows, grid_verified=True)["can_compare"]


def test_a_filling_caption_above_the_purpose_does_not_supply_subject():
    result = decide_receiving_identity(observations("收货人：另一公司"), grid_verified=True)
    assert not result["can_compare"]
    assert any(c["reason"] == "form_caption_before_purpose" for c in result["conflicts"])


def test_a_real_blank_separator_keeps_the_next_purpose_row_in_order(tmp_path):
    # A pale red divider, rather than a nonexistent OCR reading, proves blank.
    image = np.full((20, 240, 3), 255, np.uint8)
    image[8:10] = (180, 180, 250)
    path = tmp_path / "gap.png"
    cv2.imwrite(str(path), image)
    check = verify_blank_row(path, known_padding=0, reference_row_height=100)
    assert check["blank"]
    rows = observations()
    for row in rows[1:]: row["row_index"] += 1
    rows.insert(1, {"row_index": 1, "text": "", "confidence": 0,
                    "is_blank": True, "blank_check": check})
    assert decide_receiving_identity(rows, grid_verified=True)["identity_text"] == IDENTITY
    rows[1].pop("blank_check")
    assert not decide_receiving_identity(rows, grid_verified=True)["can_compare"]


def test_a_small_dark_digit_on_a_red_rule_is_not_blank():
    image = np.full((20, 240, 3), 255, np.uint8)
    image[8:10] = (180, 180, 250)
    image[4:16, 100:103] = (190, 190, 190)
    result = verify_blank_row(image, known_padding=0, reference_row_height=100)
    assert not result["blank"]
    assert result["neutral_dark_pixels"] > 0


@pytest.mark.parametrize("image", [None, np.zeros((20, 20)), np.full((20, 30, 3), np.nan)])
def test_unreadable_or_nonfinite_gap_never_is_blank(image):
    assert not verify_blank_row(image, known_padding=0, reference_row_height=100)["blank"]


def test_an_unread_whole_height_cell_is_not_a_blank_gap():
    image = np.full((100, 240, 3), 255, np.uint8)
    assert not verify_blank_row(image, known_padding=0, reference_row_height=100)["blank"]


def detector_artifact(points):
    # Canonical image padding=32 and width=960; the measured interior shares
    # the same scale here, making the physical coverage independently clear.
    return {"rectangular_frame": {"crop_box": [0, 0, 960, 400], "canonical_width": 960,
                                   "padding": 32, "detected_boxes": [
                                       {"text": "仓库名称", "confidence": .99, "points": points}]},
            "rectangular_fixed_rows": [{"crop_box": [0, 0, 960, 100], "coordinate_space": "oriented"},
                                       {"crop_box": [0, 100, 960, 200], "coordinate_space": "oriented"}]}


def test_detector_box_crossing_two_physical_rows_is_not_assigned_by_its_center():
    artifact = detector_artifact([[50, 92], [700, 92], [700, 172], [50, 172]])
    assert map_receiving_detections(artifact) == []
    # A caller cannot lower the 80% safety floor.
    assert map_receiving_detections(artifact, minimum_vertical_overlap=.1) == []
    artifact["rectangular_frame"]["detected_boxes"][0]["points"] = [[50, 52], [700, 52], [700, 112], [50, 112]]
    mapped = map_receiving_detections(artifact)
    assert len(mapped) == 1 and mapped[0]["row_index"] == 0
    assert mapped[0]["vertical_overlap"] == 1


@pytest.mark.parametrize("points", [[], [[0, 0]]*4, [[float("nan"), 0]]*4])
def test_invalid_detector_geometry_is_not_mapped(points):
    assert map_receiving_detections(detector_artifact(points)) == []


def test_nearby_text_outside_the_stamp_frame_is_not_a_same_row_detection():
    artifact = detector_artifact([[-200, 52], [-50, 52], [-50, 112], [-200, 112]])
    assert map_receiving_detections(artifact) == []


def test_verified_grid_proof_requires_both_actual_image_reads_and_unique_direction():
    record = grid_record()
    alternatives = verified_grid_alternatives([record])
    assert len(alternatives) == 1
    assert alternatives[0]["dual_representation_verified"]
    bad = copy.deepcopy(record)
    bad["accepted"] = False
    assert not verified_grid_alternatives([bad])
    bad = copy.deepcopy(record)
    bad["directions"][1] = copy.deepcopy(bad["directions"][0])
    bad["directions"][1]["direction"] = 180
    assert not verified_grid_alternatives([bad])


@pytest.mark.parametrize("change", ["missing_gray", "low_gray", "different_code", "different_number"])
def test_one_good_grid_view_cannot_hide_a_second_bad_view(change):
    record = grid_record()
    rows = record["directions"][0]["rows"]
    if change == "missing_gray": rows[0]["variants"] = rows[0]["variants"][:1]
    if change == "low_gray": rows[0]["variants"][1]["confidence"] = .70
    if change == "different_code": rows[0]["variants"][1]["text"] = "广州花东仓030DCH"
    if change == "different_number": rows[1]["variants"][1]["text"] = "收货专用章②"
    assert not verified_grid_alternatives([record])


@pytest.mark.parametrize("change", ["replaced_identity", "duplicate_view", "duplicate_row", "unknown_direction"])
def test_grid_proof_cannot_be_relabelled_or_hide_duplicate_observations(change):
    record = grid_record()
    direction = record["directions"][0]
    if change == "replaced_identity": direction["decision"]["identity_text"] = "广州另一仓020DCH收货专用章①"
    if change == "duplicate_view": direction["rows"][0]["variants"].append(dict(direction["rows"][0]["variants"][0]))
    if change == "duplicate_row": direction["rows"].append(copy.deepcopy(direction["rows"][0]))
    if change == "unknown_direction": direction["direction"] = 90
    assert not verified_grid_alternatives([record])


def test_a_useful_primary_is_retained_even_when_it_does_not_match_the_target():
    primary = decide_receiving_identity(observations("广州另一仓020DCH"), grid_verified=True)
    result = choose_receiving_identity(primary, observations(), verified_grid_alternatives([grid_record()]))
    assert result["source"] == "primary"
    assert compare_seal_text_strict(IDENTITY, [result["text"]])["status"] != "匹配"


def test_only_insufficient_primary_evidence_can_use_grid_recovery():
    alternatives = verified_grid_alternatives([grid_record()])
    result = choose_receiving_identity(weak_primary(), observations(), alternatives)
    assert result["can_compare"] and result["text"] == IDENTITY
    bad = weak_primary()
    bad["conflicts"] = [{"reason": "disagreeing_whole_row_observations"}]
    assert not choose_receiving_identity(bad, observations(), alternatives)["can_compare"]
    # A caller cannot bypass the actual geometry/dual-view proof with a bool.
    assert not choose_receiving_identity(weak_primary(), observations(), [{"can_compare": True, "identity_text": IDENTITY}])["can_compare"]


@pytest.mark.parametrize("old", ["广州另一仓020DCH", "广州花东仓030DCH", "广州花东仓O20DCH", "收货专用章②"])
def test_older_reliable_identity_ink_cannot_be_overwritten(old):
    result = choose_receiving_identity(weak_primary(), observations(), verified_grid_alternatives([grid_record()]),
                                       older_observations=[{"text": old, "confidence": .99}])
    assert not result["can_compare"]
    assert result["reason"] == "older_reliable_fragment_conflict"


def test_old_receiving_form_values_are_not_used_as_identity_fragments():
    rows = observations()
    artifact = {"reads": [dict(row, rectangle_row_index=row["row_index"]) for row in rows]}
    old = older_identity_observations(artifact)
    assert len(old) == 2
    result = choose_receiving_identity(weak_primary(), rows, verified_grid_alternatives([grid_record()]),
                                       older_observations=old)
    assert result["can_compare"]


def test_reliable_unanchored_text_prevents_grid_replacement():
    primary = dict(weak_primary(), purpose_row_index=None)
    assert not choose_receiving_identity(primary, observations(), verified_grid_alternatives([grid_record()]))["can_compare"]
    assert choose_receiving_identity(primary, [], verified_grid_alternatives([grid_record()]))["can_compare"]
