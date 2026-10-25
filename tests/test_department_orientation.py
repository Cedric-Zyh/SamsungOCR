"""A department may locate a rotation, but cannot replace purpose evidence."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import department_orientation as department
from receipt_ocr.recognition.seal.preprocess import detected_layout


def fixture_stamp(tmp_path):
    image = np.full((400, 400, 3), 255, np.uint8)
    cv2.circle(image, (200, 200), 180, (80, 80, 255), 4)
    boxes = [
        dict(text='仓储部', confidence=.99, angle=0,
             points=[[140, 150], [260, 150], [260, 185], [140, 185]]),
        dict(text='收货章', confidence=.99, angle=0,
             points=[[110, 230], [290, 230], [290, 270], [110, 270]]),
    ]
    for x, y in [(150, 160), (190, 160), (235, 160), (120, 240), (190, 240), (260, 240)]:
        cv2.rectangle(image, (x, y), (x+10, y+15), (80, 80, 255), -1)
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    return source, image, boxes


@pytest.mark.parametrize('failure', ['', 'missing_type', 'wrong_department', 'tilted_type',
                                      'weak_type', 'failed_fresh_read'])
def test_department_requires_complete_consistent_fresh_rows(tmp_path, monkeypatch, failure):
    source, image, boxes = fixture_stamp(tmp_path)
    anchor = dict(boxes[0])
    if failure == 'missing_type':
        boxes.pop()
    elif failure == 'wrong_department':
        boxes[0] = {**boxes[0], 'text': '财务部'}
    elif failure == 'tilted_type':
        boxes[1]['points'] = [[110, 220], [290, 240], [290, 280], [110, 260]]
    elif failure == 'weak_type':
        boxes[1]['confidence'] = .6

    class Reader:
        def detect_boxes(self, *a, **k):
            return boxes

        def read_line(self, path, **k):
            text = '仓储部' if 'row-0' in str(path) else '收货章'
            if failure == 'failed_fresh_read':
                text = '收货'
            return [SimpleNamespace(text=text, confidence=.99)]

    monkeypatch.setattr(department, 'OcrReader', Reader)
    before = source.read_bytes()
    item = dict(angle=0, candidate_width=400, candidate_height=400, boxes=[anchor])
    trial = department.prepare_department_candidate(source, tmp_path, tmp_path/'bands', item, model_variant='v6')
    assert (trial is not None) == (failure == '')
    assert source.read_bytes() == before
    if trial:
        assert trial['band_text'] == '收货章'
        assert trial['department_verified_rows']['image_size'] == [400, 400]


def test_verified_rows_are_reused_and_only_their_ink_is_masked(tmp_path, monkeypatch):
    source, image, boxes = fixture_stamp(tmp_path)
    verified = dict(coordinate_frame='final_oriented_stamp', image_size=[400, 400],
                    boxes=boxes, department_text='仓储部', type_text='收货章')
    guide = department.fitted_guide(image)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx-200)/180, (yy-200)/180)
    monkeypatch.setattr(detected_layout, '_detect_layout_rows',
                        lambda *a, **k: pytest.fail('complete rows must not be detected again'))
    plan = detected_layout.prepare_detected_layout(source, tmp_path/'out', 0, image, guide, rho,
                                                   reader=None, provider='paddle_v6', verified_rows=verified)
    assert plan['method'] == 'ellipse_department_rows'
    assert [r['detected_text'] for r in plan['rows']] == ['仓储部', '收货章']
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.asarray(b['points'], np.int32) for b in boxes], 255)
    after = cv2.imread(str(plan['ring_input']))
    assert np.array_equal(after[mask == 0], image[mask == 0])
    assert np.all(after[160:175, 150:160] == 255)


def test_wrong_coordinate_frame_cannot_reuse_verified_rows(tmp_path, monkeypatch):
    source, image, boxes = fixture_stamp(tmp_path)
    called = []
    def detect(*a, **k):
        called.append(True)
        return [], []
    monkeypatch.setattr(detected_layout, '_detect_layout_rows', detect)
    guide = department.fitted_guide(image)
    plan = detected_layout.prepare_detected_layout(source, tmp_path/'out', 0, image, guide,
        np.zeros(image.shape[:2]), reader=None, provider='paddle_v6',
        verified_rows=dict(coordinate_frame='final_oriented_stamp', image_size=[800, 800], boxes=boxes))
    assert plan is None and called == [True]


def test_bounded_detection_keeps_complete_department_geometry(tmp_path, monkeypatch):
    from PIL import Image
    from receipt_ocr.recognition.seal.preprocess.detection_scale import detect_orientation_boxes
    from receipt_ocr.recognition.seal.ocr import interface
    source = tmp_path/'large.png'
    Image.new('RGB', (1000, 1200), 'white').save(source)
    polygon = [[100, 150], [250, 180], [244, 210], [94, 180]]
    calls = []
    def detect(path, **kwargs):
        assert path != source  # Do not replace the observed complete row.
        calls.append(path)
        return [dict(text='仓储部', confidence=.99, points=polygon, angle=11.3)]
    monkeypatch.setattr(interface, 'detect_boxes', detect)
    boxes = detect_orientation_boxes(source, tmp_path, model_variant='v6')
    assert len(calls) == 1
    np.testing.assert_allclose(boxes[0]['points'], np.asarray(polygon)*[1000/427, 1200/512])
