import math
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.horizontal_consensus import (
    choose_consensus, horizontal_groups, protect_consensus_border,
    preserves_verified_row, ring_evidence_quality,
)


def trial(text="业务专用章", *, mode="existing", pad=0, seed=0, confidence=.95,
          box=(10, 20, 90, 50)):
    return dict(text=text, mode=mode, pad=pad, seed=seed, confidence=confidence, box=list(box))


def test_consensus_uses_actual_reads_and_intersects_only_winning_geometry():
    result = choose_consensus([
        trial(box=(10, 20, 90, 50)), trial(mode="soft-red", box=(8, 22, 92, 49)),
        trial(seed=1, confidence=.90, box=(200, 300, 400, 500)),
        trial("业务专用室", pad=1, confidence=.999),
    ])
    assert result["text"] == "业务专用章"
    assert result["mask_box"] == [10, 22, 90, 49]
    assert result["support"] == 3


@pytest.mark.parametrize("rows", [
    [trial()],
    [trial(), trial()],  # duplicates are not independent input contexts
    [trial(), trial(mode="soft-red", confidence=math.nan)],
    [trial(), trial(mode="soft-red", confidence=.84)],
    [trial(mode="raw"), trial(mode="raw", pad=1)],
    [trial(), trial(seed=1)],  # two unrelated rows do not establish one geometry
    [trial("某某有限公司"), trial("某某有限公司", mode="soft-red")],
    [trial(box=(0, 0, 10, 10)), trial(mode="soft-red", box=(20, 20, 30, 30))],
])
def test_uncertain_candidates_do_not_replace_the_existing_path(rows):
    assert choose_consensus(rows) is None


def test_conflicting_complete_readings_with_equal_support_are_not_silently_selected():
    assert choose_consensus([
        trial(), trial(mode="soft-red"),
        trial("收货专用章", pad=1), trial("收货专用章", pad=2),
    ]) is None


def test_split_geometry_is_retained_despite_ocr_misreading_the_words():
    boxes = [
        {"text": "王川集后去服音", "points": [[190, 680], [820, 690], [820, 800], [190, 790]]},
        {"text": "言后专地早", "points": [[320, 770], [785, 760], [788, 875], [322, 885]]},
        {"text": "另一行字", "points": [[200, 100], [800, 100], [800, 230], [200, 230]]},
    ]
    groups = horizontal_groups(boxes, 1000)
    assert [len(group["boxes"]) for group in groups] == [2, 1]


def test_vertical_or_invalid_boxes_are_not_horizontal_seeds():
    assert horizontal_groups([
        {"text": "业务专用章", "points": [[10, 10], [12, 90], [30, 90], [30, 10]]},
        {"text": "业务专用章", "points": [[float("nan"), 0]] * 4},
    ], 100) == []


def test_border_restoration_never_changes_unselected_pixels(tmp_path):
    image = np.full((100, 100, 3), 255, np.uint8)
    ellipse = ((50., 50.), (80., 80.), 0.)
    cv2.ellipse(image, ellipse, (40, 40, 255), 3)
    image[65:75, 25:75] = (90, 90, 255)
    source, destination, mask_path = (tmp_path / name for name in ("source.png", "out.png", "mask.png"))
    cv2.imwrite(str(source), image)
    mask = np.zeros((100, 100), np.uint8)
    mask[65:95, 20:80] = 255
    cv2.imwrite(str(mask_path), mask)
    guide = SimpleNamespace(annulus=((80., 0., ellipse), None, True))
    protect_consensus_border(source, destination, mask_path, guide)
    output, actual = cv2.imread(str(destination)), cv2.imread(str(mask_path), 0)
    assert np.array_equal(output[mask == 0], image[mask == 0])
    assert np.all(output[actual > 0] == 255)
    assert actual[70, 50] == 255
    assert np.array_equal(output[90, 50], image[90, 50])


def test_consensus_must_not_shorten_an_existing_verified_line():
    assert not preserves_verified_row("三星售后服务专用章", "售后服务专用章")
    assert preserves_verified_row("售后服务专用章", "三星售后服务专用章")
    assert preserves_verified_row("手机售后专用章", "手机售后专用章")
    assert preserves_verified_row("", "手机售后专用章")


def test_new_colour_input_cannot_erase_existing_ring_ink_or_flood_it_with_stripes():
    source = np.full((50, 100, 3), 255, np.uint8)
    source[10:20, 10:90] = (30, 30, 255)
    mx, my = np.meshgrid(np.arange(100, dtype=np.float32), np.arange(50, dtype=np.float32))
    guide = SimpleNamespace(map_x=mx, map_y=my)
    assert ring_evidence_quality(source, source, guide)["accepted"]
    erased = source.copy()
    erased[:, :60] = 255
    assert not ring_evidence_quality(source, erased, guide)["accepted"]
    flooded = source.copy()
    flooded[::2] = (20, 20, 255)
    assert not ring_evidence_quality(source, flooded, guide)["accepted"]
