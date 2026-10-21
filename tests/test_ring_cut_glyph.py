"""A low, detached stroke at the periodic seam is still part of a glyph."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.ellipse import _align_complete_ring_seam, _trim_ring_whitespace
from receipt_ocr.recognition.seal.postprocess.ring_text import reorder_ring_text


@pytest.mark.parametrize("scale", [.5, 1, 2])
def test_split_two_horizontal_strokes_survive_alignment_and_trim(scale):
    image = np.full((200, 1400), 255, np.uint8)
    for x in range(130, 1300, 140):
        cv2.rectangle(image, (x, 40), (x + 85, 170), 70, 4)
    # “二” split at the seam; each component is much shorter than .20 h.
    for y in (65, 150):
        image[y:y + 6, :60] = 60
        image[y:y + 6, -60:] = 60
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    shifted = _align_complete_ring_seam(image)
    assert not np.array_equal(shifted, image)
    # No pixel values are lost or synthesized by selecting a different seam.
    assert np.array_equal(np.sort(shifted.ravel()), np.sort(image.ravel()))
    trimmed = _trim_ring_whitespace(shifted, preserve_short_strokes=True)
    assert np.count_nonzero(trimmed == 60) == np.count_nonzero(image == 60)
    _, _, stats, _ = cv2.connectedComponentsWithStats((trimmed == 60).astype('uint8'))
    assert len(stats) - 1 == 2


def test_short_edge_strokes_survive_even_without_a_safe_seam():
    image = np.full((200, 1400), 255, np.uint8)
    for x in (200, 500, 800):
        image[40:170, x:x+70] = 0
    image[80:85, :70] = 60
    image[80:85, -70:] = 60
    trimmed = _trim_ring_whitespace(image)
    assert trimmed.shape[1] == image.shape[1]
    assert np.count_nonzero(trimmed == 60) == 700


def test_partial_arc_with_clear_ends_does_not_move():
    image = np.full((200, 1400), 255, np.uint8)
    image[35:175, 200:600] = 70
    image[35:175, 800:1200] = 70
    assert np.array_equal(_align_complete_ring_seam(image), image)


def test_gap_below_short_upper_glyphs_is_not_a_safe_cut():
    image = np.full((200, 1400), 255, np.uint8)
    image[100:170] = 50
    image[100:170, 500:800] = 255
    image[10:40, 500:800] = 50
    assert np.array_equal(_align_complete_ring_seam(image), image)


def test_split_outer_border_does_not_trigger_glyph_repair():
    image = np.full((200, 1400), 255, np.uint8)
    image[:15, :200] = 60
    image[:15, -200:] = 60
    image[45:175, 300:1000] = 70
    assert np.array_equal(_align_complete_ring_seam(image), image)


def test_ordinary_partial_arc_retains_the_original_crop():
    image = np.full((200, 1400), 255, np.uint8)
    for x in (200, 500, 800):
        image[40:170, x:x+70] = 0
    # Short noise outside the text must not expand a normal partial arc.
    image[70:75, 30:45] = 60
    assert np.array_equal(_trim_ring_whitespace(image), image[32:178, 180:890])


@pytest.mark.parametrize('branch', ['第二分公司', '第十二分公司', '天津分公司', '第分公司'])
def test_parent_and_branch_order_without_inserting_glyphs(branch):
    expected = '南京万泓电子有限公司' + branch
    raw = expected[-3:] + expected[:-3]
    result = reorder_ring_text(raw)
    assert result['text'] == expected
    assert sorted(result['text']) == sorted(raw)


def test_missing_branch_number_is_not_filled_from_requirement():
    result = reorder_ring_text('分公司南京万泓电子有限公司第', '南京万泓电子有限公司第二分公司')
    assert result['text'] == '南京万泓电子有限公司第分公司'
    assert '二' not in result['text']
