from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.number_mask import white_verified_number


def fixture(tmp_path):
    image = np.full((100, 160, 3), 255, np.uint8)
    for x in [65, 77, 89]:
        cv2.rectangle(image, (x, 60), (x+4, 74), (40, 40, 255), -1)
    # Border crosses the crop and must remain, as must exterior company ink.
    cv2.line(image, (20, 78), (140, 78), (40, 40, 255), 2)
    cv2.rectangle(image, (115, 30), (125, 50), (40, 40, 255), -1)
    ring = tmp_path/'ring.png';cv2.imwrite(str(ring), image)
    number = tmp_path/'number.png';cv2.imwrite(str(number), image[55:82, 60:100])
    combined = tmp_path/'mask.png'
    mask = np.zeros(image.shape[:2], np.uint8);mask[20:25, 50:80] = 255
    cv2.imwrite(str(combined), mask)
    return image, ring, number, combined, mask


def reader(text='(2)', confidence=.99):
    return SimpleNamespace(read_line=lambda *args, **kwargs: [SimpleNamespace(text=text, confidence=confidence)])


def test_number_removed_before_unwrap_without_whitening_border_or_source_crop(tmp_path):
    original, ring, number, combined, previous = fixture(tmp_path)
    source_pixels = cv2.imread(str(number))
    evidence = {'status':'detected', 'text':'(2)', 'confidence':.98, 'box':[60,55,100,82]}
    result = white_verified_number(ring, number, evidence, tmp_path/'number-mask.png', combined,
                                  reader=reader(), provider='paddle_v6')
    assert result['status'] == 'applied'
    after = cv2.imread(str(ring)); mask = cv2.imread(str(tmp_path/'number-mask.png'), 0)
    assert np.all(after[60:75,65:70] == 255)
    np.testing.assert_array_equal(after[76:80], original[76:80])
    np.testing.assert_array_equal(after[:,100:], original[:,100:])
    np.testing.assert_array_equal(cv2.imread(str(number)), source_pixels)
    np.testing.assert_array_equal(cv2.imread(str(combined), 0), previous | mask)
    np.testing.assert_array_equal(np.any(after != original, axis=2), mask > 0)


@pytest.mark.parametrize('text,score', [('(3)', .99), ('(2)', .5), ('字2', .99)])
def test_unverified_number_never_erased(tmp_path, text, score):
    original, ring, number, combined, _ = fixture(tmp_path)
    evidence = {'status':'detected', 'text':'(2)', 'confidence':.98, 'box':[60,55,100,82]}
    result = white_verified_number(ring, number, evidence, tmp_path/'number-mask.png', combined,
                                  reader=reader(text, score), provider='paddle_v6')
    assert result['status'] == 'unverified'
    np.testing.assert_array_equal(cv2.imread(str(ring)), original)


def test_parenthesis_tip_outside_detector_box_is_removed_in_full(tmp_path):
    _, ring, number, combined, _ = fixture(tmp_path)
    image = cv2.imread(str(ring))
    cv2.rectangle(image, (95, 52), (98, 72), (40, 40, 255), -1)
    cv2.imwrite(str(ring), image)
    evidence = {'status':'detected', 'text':'(2)', 'confidence':.98, 'box':[60,55,100,82]}
    result = white_verified_number(ring, number, evidence, tmp_path/'number-mask.png', combined,
                                  reader=reader(), provider='paddle_v6')
    after = cv2.imread(str(ring))
    assert np.all(after[52:73,95:99] == 255)
    np.testing.assert_array_equal(after[76:80], image[76:80])
    assert result['box'][1] < result['detected_box'][1]
