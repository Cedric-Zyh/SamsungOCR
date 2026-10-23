import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal.ocr import interface
from receipt_ocr.recognition.seal.preprocess import rectangle_orientation, rectangle_rows
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry


def boxes():
    return [
        {'text': 'C047497-881', 'confidence': .52, 'angle': -10,
         'points': [[10, 25], [390, 25], [390, 55], [10, 55]]},
        {'text': '三售售894', 'confidence': .48, 'angle': -10,
         'points': [[15, 95], [385, 95], [385, 135], [15, 135]]},
    ]


def make_case(monkeypatch, tmp_path, angle=0):
    source, target = tmp_path/'color.png', tmp_path/'oriented.png'
    image = np.full((180, 400, 3), 255, np.uint8)
    image[25:55, 10:390] = (40, 50, 230)
    image[95:135, 15:385] = (60, 70, 240)
    image[30:40, 20:30] = (0, 0, 200)
    cv2.imwrite(str(source), image)
    calls = []
    def detect(*a, **kw):
        calls.append(a[0])
        return boxes()
    monkeypatch.setattr(interface, 'detect_boxes', detect)
    monkeypatch.setattr(rectangle_orientation, 'choose_rectangular_stamp_angle',
                        lambda *a: {'applied_rotation': angle})
    return source, target, image, calls


def observation(text, confidence=.99):
    return [TextObservation(text, confidence, 0, 0, 1, 1)]


@pytest.mark.parametrize('angle', [0, 8, -10])
def test_opposite_direction_updates_whole_image_and_same_row_coordinates(monkeypatch, tmp_path, angle):
    source, target, image, calls = make_case(monkeypatch, tmp_path, angle)
    def read(path, **kwargs):
        if path.stem.endswith('-0'):
            return observation('三842', .55)
        return observation('C0477-8345921' if '-direction-0-' in path.stem else '三星售后6188342站')
    monkeypatch.setattr(interface, 'read_line', read)
    _, decision = rectangle_orientation.prepare_rectangular_stamp(source, target)
    assert len(calls) == 1
    assert decision['direction_check']['selected_rotation'] == 180
    assert decision['applied_rotation'] == angle + 180
    pad = decision['padding']
    padded = cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255,255,255))
    matrix = np.array([[1.,0.,0.],[0.,1.,0.]])
    if angle:
        matrix, w, h = _rotation_geometry(padded.shape[1], padded.shape[0], angle)
        padded = cv2.warpAffine(padded, matrix, (w,h), flags=cv2.INTER_CUBIC,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(255,255,255))
    np.testing.assert_array_equal(cv2.imread(str(target)), cv2.rotate(padded, cv2.ROTATE_180))
    rows, rotated = rectangle_rows.prepare_fixed_rectangle_rows(source, target, decision, tmp_path, 0)
    assert rotated and len(rows) == 2  # Original weak rows must not be filtered out.
    assert [row['text'] for row in rows] == ['三星售后6188342站', 'C0477-8345921']
    for row in rows:
        points = np.array(boxes()[row['detection_index']]['points'], np.float32) + pad
        expected = cv2.transform(points[None], matrix)[0]
        expected = [padded.shape[1]-1, padded.shape[0]-1] - expected
        np.testing.assert_allclose(row['points'], expected, atol=1e-4)


def test_confident_upright_chinese_does_not_reread_or_flip(monkeypatch, tmp_path):
    source, target, _, _ = make_case(monkeypatch, tmp_path)
    good = boxes();good[1].update(text='三星售后6188342站', confidence=.99)
    monkeypatch.setattr(interface, 'detect_boxes', lambda *a, **k: good)
    monkeypatch.setattr(interface, 'read_line', lambda *a, **k: pytest.fail('Already upright'))
    _, d = rectangle_orientation.prepare_rectangular_stamp(source, target)
    assert d['direction_check']['selected_rotation'] == 0
    assert not d.get('force_row_read')


@pytest.mark.parametrize('case', ['digits', 'weak', 'conflict', 'error'])
def test_ambiguous_or_numeric_evidence_never_flips(monkeypatch, tmp_path, case):
    source, target, _, _ = make_case(monkeypatch, tmp_path)
    def read(path, **kwargs):
        if case == 'error':
            raise RuntimeError('unavailable reader')
        if case == 'digits':
            return observation('0477-8345921')
        if case == 'weak':
            return observation('三星售后6188342站', .60)
        return observation('三星售后6188342站')
    monkeypatch.setattr(interface, 'read_line', read)
    _, d = rectangle_orientation.prepare_rectangular_stamp(source, target)
    assert d['direction_check']['selected_rotation'] == 0
    assert d['applied_rotation'] == 0
    if case == 'conflict':
        assert d['force_row_read']
        assert '待确认' in d['status']
        rows, must_read = rectangle_rows.prepare_fixed_rectangle_rows(source, target, d, tmp_path, 0)
        assert must_read and len(rows) == 2
    else:
        assert '正反方向' in d['status']
