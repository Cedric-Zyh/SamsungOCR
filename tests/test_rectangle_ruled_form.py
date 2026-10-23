from copy import deepcopy
import json

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.rectangle_frame import row_spans
from receipt_ocr.recognition.seal.preprocess.rectangle_layout import _empty_form_field
from receipt_ocr.recognition.seal.postprocess.rectangle_form import mark_empty_receiving_fields


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_rule_grid_produces_complete_cells_and_keeps_faint_number(scale):
    image = np.full((600, 800, 3), 255, np.uint8)
    for y in (150, 300, 450):
        image[y-4:y+5] = (70, 70, 245)
        image[y-4:y+5, 400:410] = 255  # A short break in the rule.
    for y in (25, 180, 325, 480):
        for x in (35, 85, 135, 185):
            image[y:y+60, x:x+30] = (80, 80, 245)
    cv2.circle(image, (720, 220), 20, (230, 230, 255), 3)
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    original = image.copy()
    spans = row_spans(image)
    assert len(spans) == 4
    for (top, bottom), y in zip(spans, (25, 180, 325, 480)):
        assert top <= y*scale and bottom >= (y+60)*scale
    assert np.any(np.all(image[spans[1][0]:spans[1][1]] == (230, 230, 255), axis=2))
    np.testing.assert_array_equal(image, original)
    json.dumps(spans)  # Must persist as ordinary Python coordinates.


def test_rules_alone_are_not_ocr_rows():
    image = np.full((600, 800, 3), 255, np.uint8)
    for y in (150, 300, 450):
        image[y-4:y+5] = (70, 70, 245)
    assert row_spans(image) == []


def detection(text, rect, confidence=.99):
    l,t,r,b = rect
    # Mirror the actual canonical image mapping in prepare_frame_layout.
    points = np.array([[l,t],[r,t],[r,b],[l,b]]) * 1.2 + 32
    return {'text': text, 'confidence': confidence, 'points': points.tolist()}


def caption_image():
    image = np.full((600, 800, 3), 255, np.uint8)
    image[325:385, 35:205] = (190,190,245)
    return image, detection('收货人', [30,320,210,390])


def test_empty_caption_requires_both_independent_label_and_empty_value_pixels():
    image, label = caption_image()
    field = _empty_form_field(image, (310,440), [label], 720)
    assert field['label'] == '收货人'
    image[325:375, 400:440] = (70,70,245)
    assert _empty_form_field(image, (310,440), [label], 720) is None


@pytest.mark.parametrize('case', ['weak_label', 'wrong_label', 'observed_value', 'invalid_geometry'])
def test_unconfirmed_or_filled_cells_are_never_marked_empty(case):
    image, label = caption_image()
    boxes = [label]
    if case == 'weak_label':
        label['confidence'] = .70
    elif case == 'wrong_label':
        label['text'] = '其他科技有限公司'
    elif case == 'invalid_geometry':
        label['points'] = [[0,0]] * 4
    else:
        boxes.append(detection('张三', [400,325,440,375]))
    assert _empty_form_field(image, (310,440), boxes, 720) is None


def test_date_placeholders_are_allowed_without_inventing_actual_dates():
    image = np.full((600,800,3),255,np.uint8)
    image[480:540, 35:235] = (70,70,245)
    image[480:540, 400:650] = (70,70,245)
    boxes = [detection('收货日期：', [30,475,240,545]),
             detection('年月日', [395,475,655,545])]
    assert _empty_form_field(image, (465,600), boxes, 720)['label'] == '收货日期：'
    boxes[-1]['text'] = '2026年10月23日'
    assert _empty_form_field(image, (465,600), boxes, 720) is None


def test_misread_empty_caption_stays_raw_but_does_not_become_stamp_name():
    a = {'rectangular_layout':'frame_rows',
         'rectangular_fixed_rows':[{}, {}, {'empty_form_field':{
             'label':'收货人', 'method':'detected_caption_and_empty_ink'}}],
         'reads':[{'channel':'body','text':'收货专用章①','confidence':.99,
                   'image_url':'/type.png','rectangle_row_index':1},
                  {'channel':'body','text':'收贫人：','confidence':.97,
                   'image_url':'/field.png','rectangle_row_index':2}]}
    raw = deepcopy(a['reads'])
    mark_empty_receiving_fields(a)
    assert [r['text'] for r in a['reads']] == [r['text'] for r in raw]
    assert a['reads'][1]['rectangle_field_kind'] == 'empty_receiving_field'
    assert not a['reads'][1]['matching']
    assert a['rectangular_form_fields'][0]['text'] == '收贫人：'
