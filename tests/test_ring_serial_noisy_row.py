"""Noisy lower background must not hide a geometrically supported serial."""
import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.ring_serial import locate_ring_serial


def noisy_row():
    image = np.full((240, 2400, 3), 255, np.uint8)
    cv2.putText(image, 'COMPANY', (1000, 220), cv2.FONT_HERSHEY_SIMPLEX,
                6, (0, 0, 0), 6)
    for i, digit in enumerate('210242003052667'):
        cv2.putText(image, digit, (170 + i * 45, 65), cv2.FONT_HERSHEY_SIMPLEX,
                    1.5, (0, 0, 0), 3)
    # Merge the middle five glyphs into a wide component, leaving two
    # independent regular runs (four and six glyphs) as localization evidence.
    cv2.line(image, (170 + 4 * 45, 45), (170 + 8 * 45 + 35, 45), (0, 0, 0), 3)
    # This is outside the number row, but fails the old whole-area blank gate.
    cv2.rectangle(image, (165, 110), (855, 145), (0, 0, 0), -1)
    return image


@pytest.mark.parametrize('scale', [.5, 1, 2])
@pytest.mark.parametrize('phase', [0, 400, 1500])
def test_two_runs_locate_complete_row_without_changing_pixels(scale, phase):
    image = cv2.resize(noisy_row(), None, fx=scale, fy=scale)
    image = np.roll(image, round(phase * scale), axis=1)
    original = image.copy()
    found = locate_ring_serial(image)
    assert found is not None
    assert found['localization'] == 'two_regular_runs'
    assert found['glyph_count'] == 15
    left, top, right, bottom = found['crop_box']
    shifted = np.roll(image, found['shift'], axis=1)
    crop = shifted[top:bottom, left:right]
    assert bottom < 110 * scale
    assert crop.size > 0
    assert np.array_equal(image, original)


@pytest.mark.parametrize('case', ['missing_middle', 'no_second_run', 'wrong_baseline'])
def test_rejects_unsupported_extension(case):
    image = noisy_row()
    if case == 'missing_middle':
        image[:85, 170 + 5 * 45:170 + 6 * 45] = 255
    elif case == 'no_second_run':
        image[:85, 160:170 + 4 * 45] = 255
    else:
        part = image[:85, 160:170 + 4 * 45].copy()
        image[:85, 160:170 + 4 * 45] = 255
        image[35:120, 160:170 + 4 * 45] = part
    assert locate_ring_serial(image) is None
