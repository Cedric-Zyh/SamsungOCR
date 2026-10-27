from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal.preprocess import horizontal_brand_rows as module


def sample(tmp_path, monkeypatch):
    image = np.full((600, 800, 3), 255, np.uint8)
    cv2.ellipse(image, (400, 300), (360, 260), 0, 0, 360, (0, 0, 220), 5)
    image[100:140, 300:500] = (0, 0, 220)  # company arc
    image[280:320, 240:560] = (0, 0, 220)  # Latin brand
    image[410:450, 280:520] = (0, 0, 220)  # stamp purpose
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), image)
    theta = np.linspace(0, 2*np.pi, 720, endpoint=False)
    guide = SimpleNamespace(
        points=np.column_stack((400+360*np.cos(theta), 300+260*np.sin(theta))),
        annulus=[(None, None, ((400, 300), (720, 520), 0))],
    )
    monkeypatch.setattr(module, 'trace_ellipse_border', lambda *a, **kw: guide)
    boxes = [
        dict(text='SAMSUNG', confidence=.999, points=[[235,275],[565,275],[565,325],[235,325]]),
        dict(text='服务专用章', confidence=.999, points=[[275,405],[525,405],[525,455],[275,455]]),
    ]
    calls = []

    def read_line(path, **kwargs):
        calls.append(path)
        text = 'SAMSUNG' if path.stem.endswith('-0') else '服务专用章'
        return [TextObservation(text, .999, 0, 0, 1, 1)]

    reader = SimpleNamespace(detect_boxes=lambda *a, **kw: deepcopy(boxes), read_line=read_line)
    orientation = dict(type_band_text='服务专用章', oriented_type_row_exact_box=[275,405,525,455])
    return source, image, boxes, reader, orientation, calls


def test_independent_rows_read_and_mask_without_touching_arc_or_rim(tmp_path, monkeypatch):
    source, original, boxes, reader, orientation, calls = sample(tmp_path, monkeypatch)
    result = module.prepare_horizontal_brand_rows(source, tmp_path/'out', 0, orientation,
                                                  reader=reader, provider='paddle_v6')
    assert result is not None
    assert [row['text'] for row in result['rows']] == ['SAMSUNG', '服务专用章']
    assert len(calls) == 2
    actual = cv2.imread(str(result['masked']))
    assert np.all(actual[280:320, 240:560] == 255)
    assert np.all(actual[410:450, 280:520] == 255)
    allowed = np.zeros(original.shape[:2], np.uint8)
    for box in boxes:
        cv2.fillPoly(allowed, [np.array(box['points'])], 255)
    np.testing.assert_array_equal(actual[allowed == 0], original[allowed == 0])
    np.testing.assert_array_equal(cv2.imread(str(source)), original)
    np.testing.assert_array_equal(cv2.imread(str(result['mask']), 0) > 0,
                                  np.any(actual != original, axis=2))


@pytest.mark.parametrize('case', ['no_brand', 'number', 'faint', 'arc', 'rim', 'duplicate_type',
                                  'partial', 'different_type', 'uncertain_read', 'wrong_read'])
def test_unverified_rows_fall_back_without_changing_source(tmp_path, monkeypatch, case):
    source, original, boxes, reader, orientation, calls = sample(tmp_path, monkeypatch)
    if case == 'no_brand':
        boxes.pop(0)
    elif case == 'number':
        boxes[0]['text'] = '(1)'
    elif case == 'faint':
        boxes[0]['confidence'] = .8
    elif case == 'arc':
        boxes[0]['points'] = [[235,95],[565,95],[565,145],[235,145]]
    elif case == 'rim':
        boxes[0]['points'] = [[15,275],[565,275],[565,325],[15,325]]
    elif case == 'duplicate_type':
        boxes.append(deepcopy(boxes[-1]))
    elif case == 'partial':
        orientation['partial_anchor'] = True
    elif case == 'different_type':
        orientation['type_band_text'] = '业务专用章'
    else:
        reader.read_line = lambda *a, **kw: [TextObservation(
            'SAMSUNG' if case == 'uncertain_read' else 'OTHER',
            .8 if case == 'uncertain_read' else .999, 0, 0, 1, 1)]
    out = tmp_path/'out'
    assert module.prepare_horizontal_brand_rows(source, out, 0, orientation,
                                                 reader=reader, provider='paddle_v6') is None
    assert not (out/'seal-0-horizontal-masked.png').exists()
    np.testing.assert_array_equal(cv2.imread(str(source)), original)
