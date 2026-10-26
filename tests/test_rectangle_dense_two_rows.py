"""Complete rims and their real separator survive dense/pink service stamps."""
import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.shapes import seal_region_is_rectangular
from receipt_ocr.recognition.seal.preprocess.rectangle_frame import measure_frame, row_spans


def two_rows():
    image = np.full((380, 720, 3), (210, 210, 255), np.uint8)
    cv2.rectangle(image, (20, 20), (700, 360), (0, 0, 255), 16)
    cv2.line(image, (20, 155), (700, 155), (0, 0, 255), 14)
    for x in range(42, 665, 65):
        cv2.rectangle(image, (x, 50), (x+42, 130), (15, 15, 255), -1)
        cv2.rectangle(image, (x, 185), (x+42, 330), (15, 15, 255), -1)
    # A faint real mark is part of the name; measuring strong rims must not
    # remove it from the image handed to OCR.
    cv2.circle(image, (680, 90), 5, (230, 230, 255), -1)
    return image


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_actual_rule_separates_dense_rows_without_clipping_their_ink(scale):
    image = two_rows()
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    before = image.copy()
    rotated, (left, top, right, bottom), _ = measure_frame(image)
    spans = row_spans(rotated[top:bottom, left:right])
    assert len(spans) == 2
    assert left < 42*scale and right > 685*scale
    assert top < 50*scale and bottom > 330*scale
    assert 130*scale < top+spans[0][1] < 185*scale
    assert np.any(np.all(rotated[top:bottom, left:right] == (230, 230, 255), axis=2))
    assert np.array_equal(image, before)


def test_single_rule_without_two_ink_fields_is_not_a_two_row_stamp():
    image = np.full((320, 700, 3), 255, np.uint8)
    cv2.line(image, (0, 150), (699, 150), (0, 0, 255), 10)
    image[200:290, 70:600] = (0, 0, 255)
    spans = row_spans(image)
    assert len(spans) <= 1


def test_real_divider_does_not_collapse_four_independent_text_fields():
    image = np.full((380, 720, 3), 255, np.uint8)
    rows = [(25, 75), (105, 150), (215, 260), (295, 345)]
    for top, bottom in rows:
        for x in range(40, 670, 70):
            cv2.rectangle(image, (x, top), (x+35, bottom), (0, 0, 255), -1)
    cv2.line(image, (0, 185), (719, 185), (0, 0, 255), 8)
    spans = row_spans(image)
    containing = [next(i for i, (a, b) in enumerate(spans) if a <= top and b > bottom)
                  for top, bottom in rows]
    assert len(set(containing)) == 4


@pytest.mark.parametrize('oval', [False, True])
def test_wash_cannot_turn_an_oval_into_a_rectangle(tmp_path, oval):
    # A broad, soft tint can obscure the low-threshold contour. The actual
    # supported four corners still decide the shape.
    image = np.full((400, 760, 3), 255, np.uint8)
    cv2.ellipse(image, (380, 200), (358, 186), 0, 0, 360, (210, 210, 255), -1)
    if oval:
        cv2.ellipse(image, (380, 200), (305, 145), 0, 0, 360, (0, 0, 255), 12)
    else:
        cv2.rectangle(image, (70, 70), (690, 330), (0, 0, 255), 12)
    path = tmp_path/'stamp.png'
    cv2.imwrite(str(path), image)
    assert seal_region_is_rectangular(path, SealRegion(0, 0, 1, 1, 'red', '客户章', 0)) is (not oval)
