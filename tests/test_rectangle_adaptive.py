"""Protect faint ink and the actual pixels/coordinates after a half-turn."""
import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import rectangle_layout as layout
from receipt_ocr.recognition.seal.preprocess.rectangle_frame import _edge_rule, geometry_threshold, measure_frame, red_ink, row_spans


def detection(text, confidence=.99):
    return {"text": text, "confidence": confidence,
            "points": [[40, 40], [900, 40], [900, 150], [40, 150]]}


def test_pink_wash_and_narrow_cracks_do_not_clip_rows():
    image = np.full((400, 600, 3), (210, 210, 255), np.uint8)
    cv2.rectangle(image, (10, 10), (590, 390), (0, 0, 255), 5)
    for x in range(50, 550, 50):
        cv2.rectangle(image, (x, 50), (x + 25, 145), (0, 0, 255), -1)
        cv2.rectangle(image, (x, 230), (x + 25, 350), (0, 0, 255), -1)
    image[95:98] = 255  # A cleaned table rule crosses the first text row.
    cv2.circle(image, (560, 110), 7, (225, 225, 255), -1)
    before = image.copy()
    assert geometry_threshold(image) > 25
    rotated, (left, top, right, bottom), angle = measure_frame(image)
    interior = rotated[top:bottom, left:right]
    spans = row_spans(interior)
    assert len(spans) == 2
    assert left < 50 and right > 567 and top < 50 and bottom > 350
    assert 145 < top + spans[0][1] < 230
    # Geometry isolation never whites out the faint mark in the OCR pixels.
    assert np.any(np.all(interior == (225, 225, 255), axis=2))
    assert np.array_equal(image, before)


def test_faint_frame_keeps_low_geometry_threshold():
    image = np.full((240, 500, 3), 255, np.uint8)
    cv2.rectangle(image, (5, 5), (495, 235), (220, 220, 255), 4)
    image[50:90, 50:400] = (220, 220, 255)
    assert geometry_threshold(image) == 25
    rotated, crop, _ = measure_frame(image)
    assert crop[0] < 50 and crop[2] > 400
    assert np.any(np.all(rotated == (220, 220, 255), axis=2))


def test_boundary_rule_is_not_a_text_row():
    image = np.full((400, 800, 3), 255, np.uint8)
    image[0:9, :] = (0, 0, 255)
    for x in range(30, 750, 50):
        image[50:130, x:x+20] = (0, 0, 255)
        image[240:350, x:x+20] = (0, 0, 255)
    spans = row_spans(image)
    assert len(spans) == 2
    assert _edge_rule(red_ink(image), 0, 20)
    assert not _edge_rule(red_ink(image), 40, 130)
    assert spans[0][0] <= 50 and spans[1][1] >= 350


@pytest.mark.parametrize('opposite_text,confidence,flip', [
    ('维修服务中心', .99, True),
    ('2310637', .9999, False),
    ('维修服务中心', .60, False),
])
def test_opposite_direction_requires_text_evidence_and_saves_matching_pixels(
        tmp_path, monkeypatch, opposite_text, confidence, flip):
    source = tmp_path / 'source.png'
    image = np.full((200, 400, 3), 255, np.uint8)
    image[20:80, 30:160] = (0, 0, 255)
    image[130:180, 240:360] = (190, 190, 255)
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(layout, 'measure_frame', lambda im: (im, [10, 10, 390, 190], 0))
    monkeypatch.setattr(layout, 'row_spans', lambda im: [(0, im.shape[0])])
    calls = []

    class Reader:
        def detect_boxes(self, path, **kwargs):
            calls.append(path)
            return [detection('2310637')] if len(calls) == 1 else [detection(opposite_text, confidence)]

    meta, inputs, jobs = layout.prepare_frame_layout(source, tmp_path, 0, '/files/test', 'paddle_v6', 'combined', Reader())
    assert len(calls) == 2
    assert meta['orientation']['rotation_steps'] == ([180, 0] if flip else [0])
    expected = cv2.rotate(image, cv2.ROTATE_180) if flip else image
    assert np.array_equal(cv2.imread(str(tmp_path/'seal-0-oriented.png')), expected)
    expected_canonical, _ = layout._canonical(expected[10:190, 10:390])
    assert np.array_equal(cv2.imread(str(tmp_path/'seal-0-rectangle-layout.png')), expected_canonical)
    actual_input = cv2.imread(str(jobs[0][1]))[20:-20, 20:-20]
    assert np.array_equal(actual_input, expected[10:190, 10:390])
    assert meta['rectangular_fixed_rows'][0]['crop_box'] == [10, 10, 390, 190]


def test_invalid_detection_cannot_select_direction():
    malformed = detection('完整公司名称')
    malformed['points'] = [[float('nan'), 0]] * 4
    assert layout._direction_score([malformed]) == 0
