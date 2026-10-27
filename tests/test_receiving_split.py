from copy import deepcopy

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.postprocess.receiving_split import decide_receiving_split
from receipt_ocr.recognition.seal.preprocess.receiving_split import (
    measure_receiving_split,
    prepare_receiving_split,
)


def evidence(left="上海朱泾DC", code="021DC0"):
    metadata = {"prepared": True, "grid_verified": True, "row_index": 0,
                "source_size": [900, 150], "inputs": []}
    reads = []
    for kind in ("color", "gray"):
        for part, scale in (("left", 1), ("right", 1), ("right", 2)):
            key = f"{kind}-{part}-{scale}"
            metadata["inputs"].append({"id": key, "representation": kind,
                "split_part": part, "split_scale": scale, "rectangle_row_index": 0,
                "source_size": [900, 150], "crop_box": [30, 20, 530, 120] if part == "left" else [530, 20, 870, 120],
                "image_url": f"/files/{key}.png"})
            reads.append({"input_id": key, "text": left if part == "left" else code, "confidence": .97})
    return metadata, reads


def test_literal_code_and_name_are_not_rewritten():
    metadata, reads = evidence("北京永乐DC", "010DCM")
    result = decide_receiving_split(metadata, reads, whole_row_reads=[])
    assert result["accepted"] and result["matching_allowed"]
    assert result["title_text"] == "北京永乐DC010DCM"
    metadata, reads = evidence(code="021DCO")
    assert decide_receiving_split(metadata, reads)["title_text"] == "上海朱泾DC021DCO"
    metadata, reads = evidence("上朱泾DC")
    assert decide_receiving_split(metadata, reads)["title_text"] == "上朱泾DC021DC0"


def test_successful_segments_do_not_erase_a_reliable_whole_row_conflict():
    metadata, reads = evidence()
    result = decide_receiving_split(metadata, reads, whole_row_reads=[
        {"text": "上海朱泾DC 021DCO", "confidence": .99}])
    assert result["accepted"] and result["title_text"] == "上海朱泾DC021DC0"
    assert result["requires_review"] and not result["matching_allowed"]
    assert result["conflicts"][0]["text"].endswith("DCO")
    assert all(r["text"] != "021DCO" for r in reads)


def test_missing_whole_row_evidence_never_grants_matching_permission():
    metadata, reads = evidence()
    result = decide_receiving_split(metadata, reads)
    assert result["accepted"] and result["requires_review"]
    assert not result["matching_allowed"]
    matching = [{"text": "上海朱泾DC 021DC0", "confidence": .95}]
    assert decide_receiving_split(metadata, reads, whole_row_reads=matching)["matching_allowed"]


@pytest.mark.parametrize("changed", ["021DCO", "031DC0", "021DC2"])
def test_any_literal_code_disagreement_is_rejected(changed):
    metadata, reads = evidence()
    reads[-1]["text"] = changed
    result = decide_receiving_split(metadata, reads)
    assert not result["accepted"] and result["reason"] == "literal_segment_disagreement"


@pytest.mark.parametrize("confidence", [.89, float("nan"), float("inf"), -1, 1.1, "invalid", True])
def test_unreliable_or_nonfinite_confidence_is_rejected(confidence):
    metadata, reads = evidence()
    reads[0]["confidence"] = confidence
    assert not decide_receiving_split(metadata, reads)["accepted"]


@pytest.mark.parametrize("left,code", [
    ("上海", "朱泾DC021DC0"), ("上海朱泾DC02", "1DC0"),
    ("上海朱泾DC", "0"), ("上海朱泾DC", "021dc0"),
])
def test_cuts_inside_words_or_isolated_digits_are_not_full_code_evidence(left, code):
    metadata, reads = evidence(left, code)
    assert not decide_receiving_split(metadata, reads)["accepted"]


@pytest.mark.parametrize("mutation", ["other_row", "no_shared_edge", "other_y", "out_of_bounds", "nan_box"])
def test_geometry_cannot_join_other_rows_or_unaligned_views(mutation):
    metadata, reads = evidence()
    item = metadata["inputs"][-1]
    if mutation == "other_row": item["rectangle_row_index"] = 1
    if mutation == "no_shared_edge": item["crop_box"][0] += 1
    if mutation == "other_y": item["crop_box"][1] += 1
    if mutation == "out_of_bounds": item["crop_box"][2] = 1000
    if mutation == "nan_box": item["crop_box"][0] = float("nan")
    assert not decide_receiving_split(metadata, reads)["accepted"]


def test_incomplete_duplicate_or_unverified_inputs_cannot_form_a_candidate():
    metadata, reads = evidence()
    assert not decide_receiving_split(metadata, reads[:-1])["accepted"]
    assert not decide_receiving_split(metadata, [*reads, deepcopy(reads[0])])["accepted"]
    metadata["inputs"].append(deepcopy(metadata["inputs"][0]))
    assert not decide_receiving_split(metadata, reads)["accepted"]
    metadata, reads = evidence()
    metadata["grid_verified"] = False
    assert not decide_receiving_split(metadata, reads)["accepted"]


def test_preparation_uses_one_measured_gap_and_real_aligned_pixels(tmp_path):
    color = np.full((110, 650, 3), 255, np.uint8)
    for x in [30, 65, 100, 135, 170, 205, 240, 400, 435, 470, 505, 540, 575]:
        color[25:75, x:x+18] = (45, 45, 220)
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    color_path, gray_path = tmp_path / "color.png", tmp_path / "gray.png"
    cv2.imwrite(str(color_path), color)
    cv2.imwrite(str(gray_path), gray)
    original = color.copy()
    metadata, jobs = prepare_receiving_split(color_path, gray_path, tmp_path / "out", 2,
                                            "/files/test", grid_verified=True)
    assert metadata["prepared"] and len(jobs) == 6
    assert metadata["left_box"][2] == metadata["right_box"][0]
    assert metadata["left_box"][1::2] == metadata["right_box"][1::2]
    assert np.array_equal(cv2.imread(str(color_path)), original)
    for item, path in jobs:
        assert path.is_file() and item["matching"] is False
        source = original if item["representation"] == "color" else cv2.imread(str(gray_path))
        x, y, right, bottom = item["crop_box"]
        crop = source[y:bottom, x:right]
        if item["split_scale"] == 2:
            crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        actual = cv2.imread(str(path))
        assert np.array_equal(actual[8:-8, 8:-8], crop)


def test_preparation_refuses_unverified_or_misaligned_rows(tmp_path):
    metadata, jobs = prepare_receiving_split("missing", "missing", tmp_path, 0, "")
    assert not metadata["prepared"] and not jobs
    color, gray = tmp_path / "color.png", tmp_path / "gray.png"
    cv2.imwrite(str(color), np.full((100, 500, 3), 255, np.uint8))
    cv2.imwrite(str(gray), np.full((90, 500, 3), 255, np.uint8))
    metadata, jobs = prepare_receiving_split(color, gray, tmp_path / "out", 0, "", grid_verified=True)
    assert not jobs and metadata["reason"] == "unaligned_row_representations"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("image", [None, np.zeros((1, 1, 3), np.uint8), np.zeros((100, 200)), np.zeros((100, 200, 4), np.uint8)])
def test_malformed_measurement_input_is_rejected(image):
    assert not measure_receiving_split(image)["prepared"]
