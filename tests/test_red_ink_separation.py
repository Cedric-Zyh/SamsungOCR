"""Regressions for dark/pale stamp strokes crossing neutral printed rules."""
import cv2
import numpy as np

from receipt_ocr.imaging.colors import _isolate_red_ink


def test_neutral_print_is_removed_at_all_brightness_levels():
    pixels = np.array([[[n, n, n] for n in range(256)]], dtype=np.uint8)
    assert np.all(_isolate_red_ink(pixels) == 255)


def test_dark_red_and_pale_red_survive_without_importing_black_darkness():
    # The dark stroke falls below the old V>=50 gate; pale red below S>=45.
    pixels = np.array([[[5, 5, 35], [235, 235, 250], [24, 24, 27]]], dtype=np.uint8)
    separated = _isolate_red_ink(pixels)
    assert separated[0, 0, 0] < 80
    assert separated[0, 1, 0] < 235
    assert np.all(separated[0, 2] == 255)
    assert np.all(separated[:, :, 2] == 255)


def test_red_crossings_survive_horizontal_and_vertical_tinted_rules():
    image = np.full((180, 260, 3), 255, dtype=np.uint8)
    cv2.line(image, (5, 90), (255, 90), (35, 35, 47), 3)
    cv2.line(image, (130, 5), (130, 175), (35, 35, 47), 3)
    # Visible dark-red evidence on both rules must survive the line pass.
    image[85:96, 55:60] = (5, 5, 30)
    image[40:45, 125:136] = (5, 5, 30)
    # A pale red stroke beside a rule must not be erased by mask dilation.
    image[87, 180:195] = (235, 235, 250)
    result = _isolate_red_ink(image)
    assert np.all(result[90, 20] == 255)
    assert np.all(result[20, 130] == 255)
    assert result[90, 57, 0] < 80
    assert result[42, 130, 0] < 80
    assert result[87, 185, 0] < 235


def test_dark_red_rectangle_border_is_not_treated_as_a_table_rule():
    image = np.full((160, 300, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (290, 150), (5, 5, 30), 3)
    result = _isolate_red_ink(image)
    for y, x in [(10, 100), (100, 10), (150, 100), (100, 290)]:
        assert result[y, x, 0] < 80


def test_dark_reddish_noise_does_not_gain_more_ink_than_confirmed_red():
    pixels = np.array([[[70, 70, 75], [70, 70, 100]]], dtype=np.uint8)
    separated = _isolate_red_ink(pixels)
    assert separated[0, 0, 0] > 240
    assert separated[0, 1, 0] < 100
