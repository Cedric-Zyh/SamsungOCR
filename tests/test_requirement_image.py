import cv2
import numpy as np
import pytest
from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.fields.requirement_image import (
    requirement_crop_box, clean_and_deskew_requirement,
)


def row(text, x=.05, y=.47, width=.4, height=.02):
    return TextObservation(text, .99, x, y, width, height)


def test_complete_requirement_stops_before_adjacent_quantity():
    anchor = row('签章要求：客户科技有限公司业务专用章（1）')
    quantity = row('实收数量：', x=.58, y=.48, width=.1)
    box, reason = requirement_crop_box((2000, 3000), anchor, [anchor, quantity])
    assert box[2] > (anchor.x + anchor.width) * 2000
    assert box[2] < quantity.x * 2000
    assert reason == 'detected_requirement_row'


def test_label_only_anchor_uses_neighboring_field_as_boundary():
    anchor = row('签章要求：', width=.08)
    quantity = row('实收数量：', x=.58, y=.48, width=.1)
    box, reason = requirement_crop_box((2000, 3000), anchor, [anchor, quantity])
    assert .5 * 2000 < box[2] < quantity.x * 2000
    assert reason == 'neighbor_field'


def test_long_requirement_and_terminal_number_are_not_limited_to_old_width():
    anchor = row('签章要求：某某科技有限公司海淀第三分公司售后服务专用章（12）', width=.82)
    box, _ = requirement_crop_box((2000, 3000), anchor, [anchor])
    assert box[2] > .87 * 2000
    assert box[2] <= 2000


def test_next_row_quantity_under_authorization_code_cannot_cut_the_code():
    anchor = row('签章要求：三星授权某某服务中心授权代码:4913766', width=.55, height=.012)
    quantity = row('实收数量：4（台）', x=.565, y=.485, width=.15, height=.017)
    box, reason = requirement_crop_box((2487, 3593), anchor, [anchor, quantity])
    assert box[2] > (anchor.x + anchor.width) * 2487
    assert reason == 'detected_requirement_row'


def test_missing_anchor_keeps_template_fallback():
    assert requirement_crop_box((1000, 1000), None, [])[0] == (15, 464, 640, 483)


def test_station_and_phone_box_extend_requirement_even_if_anchor_looks_complete():
    anchor = row('签章要求:三星电子服务中心取机专用章（2）',
                 x=.0376, y=.4711, width=.3449, height=.0138)
    tail = row('6237143站电话：02081061101', x=.3734, y=.4732, width=.2204, height=.0121)
    quantity = row('实收数量：', x=.5719, y=.4891, width=.0723, height=.0134)
    box, reason = requirement_crop_box((1647, 2390), anchor, [quantity, tail, anchor])
    assert box[2] > (tail.x + tail.width) * 1647
    assert box[3] < quantity.y * 2390
    assert reason == 'detected_requirement_line'


def test_label_value_number_and_phone_can_be_multiple_boxes():
    anchor = row('签章要求：', width=.08, height=.012)
    value = row('某某有限公司收货专用章', x=.14, width=.29, height=.012)
    number = row('（12）', x=.44, width=.04, height=.010)
    phone = row('站电话：02012345678', x=.49, width=.2, height=.012)
    box, reason = requirement_crop_box((2000, 3000), anchor, [phone, number, value, anchor])
    assert box[2] > .69 * 2000
    assert reason == 'detected_requirement_line'


def test_next_line_tail_and_remote_same_height_text_are_not_joined():
    anchor = row('签章要求：某某有限公司', width=.25, height=.012)
    lower = row('如未签实收数量视为整单完整签收', x=.31, y=.485, width=.3, height=.012)
    remote = row('其他文字', x=.8, width=.1, height=.012)
    box, reason = requirement_crop_box((2000, 3000), anchor, [anchor, lower, remote])
    assert box[2] < .33 * 2000
    assert reason == 'detected_requirement_row'


def test_same_row_quantity_is_a_barrier_including_its_separate_value():
    anchor = row('签章要求：某某有限公司', width=.25, height=.012)
    quantity = row('实收数量：', x=.31, width=.07, height=.012)
    count = row('100', x=.385, width=.04, height=.012)
    box, _ = requirement_crop_box((2000, 3000), anchor, [anchor, quantity, count])
    assert box[2] < quantity.x * 2000


@pytest.mark.parametrize('angle', [-3, -1.5, 1.5, 3])
def test_text_baseline_deskew_retains_edge_characters(angle):
    image = np.full((130, 900, 3), 255, np.uint8)
    for x in range(40, 860, 40):
        cv2.rectangle(image, (x, 45), (x + 24, 84), (0, 0, 0), 3)
    # A narrow number/punctuation-shaped component after the main glyphs.
    cv2.line(image, (880, 48), (880, 82), (0, 0, 0), 3)
    matrix = cv2.getRotationMatrix2D((450, 65), angle, 1)
    tilted = cv2.warpAffine(image, matrix, (900, 130), borderValue=(255, 255, 255))
    cleaned, meta = clean_and_deskew_requirement(Image.fromarray(tilted))
    assert meta['deskew_applied']
    assert abs(meta['rotation_degrees'] + angle) < .3
    _, _, stats, _ = cv2.connectedComponentsWithStats((np.asarray(cleaned) < 100).astype('uint8'))
    glyphs = [s for s in stats[1:] if s[4] > 50]
    assert len(glyphs) == 22  # Including the terminal number.
    assert np.ptp([s[1] + s[3] for s in glyphs[:-1]]) < 5


def test_insufficient_baseline_does_not_guess_rotation():
    image = Image.new('RGB', (500, 50), 'white')
    clean, meta = clean_and_deskew_requirement(image)
    assert not meta['deskew_applied']
    assert clean.size == image.size


def test_red_seal_ink_does_not_set_printed_line_direction():
    image = np.full((100, 900, 3), 255, np.uint8)
    cv2.ellipse(image, (450, 50), (350, 40), 0, 0, 360, (255, 70, 70), 5)
    cleaned, meta = clean_and_deskew_requirement(Image.fromarray(image))
    assert not meta['deskew_applied']
    assert np.asarray(cleaned).min() == 255
