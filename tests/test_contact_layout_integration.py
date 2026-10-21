from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import detected_layout, multi_zone


def contact_boxes(overlap=4):
    return [
        {'text': '0451-53608531', 'confidence': .99,
         'points': [[180, 180], [500, 180], [500, 240], [180, 240]]},
        {'text': '维修专用章', 'confidence': .99,
         'points': [[190, 240-overlap], [490, 240-overlap],
                    [490, 300-overlap], [190, 300-overlap]]},
    ]


@pytest.mark.parametrize('scale', [1, 2])
def test_contact_rows_accept_small_margin_overlap_but_reject_shared_content(scale):
    image = np.full((500*scale, 700*scale, 3), 255, np.uint8)
    for overlap, expected in [(4, 2), (30, 0), (60, 0)]:
        boxes = contact_boxes(overlap)
        for box in boxes:
            box['points'] = (np.array(box['points']) * scale).tolist()
        assert len(detected_layout.detected_rows(
            boxes, image, np.zeros(image.shape[:2]))) == expected


def test_bounded_detection_maps_rows_back_using_actual_preview_dimensions(tmp_path):
    image = np.full((901, 1101, 3), 255, np.uint8)
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    seen = []
    boxes = contact_boxes()

    def detect(path, **kwargs):
        h, w = cv2.imread(str(path)).shape[:2]
        seen.append((w, h))
        return [{**b, 'points': (np.array(b['points']) * [w/1101, h/901]).tolist()}
                for b in boxes]

    _, rows = detected_layout._detect_layout_rows(
        source, tmp_path/'out', 0, image, np.zeros(image.shape[:2]),
        reader=SimpleNamespace(detect_boxes=detect), provider='paddle_v6')
    assert seen == [(512, 419)]
    assert len(rows) == 2
    for row, box in zip(rows, boxes):
        np.testing.assert_allclose(row['polygon'], box['points'])


def test_complete_detector_rows_take_priority_over_fragmented_component_rows(tmp_path, monkeypatch):
    image = np.full((400, 600, 3), 255, np.uint8)
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    from receipt_ocr.recognition.seal.preprocess import station_layout
    monkeypatch.setattr(station_layout, 'prepare_station_layout', lambda *a, **k: None)
    guide = SimpleNamespace(annulus=[(0, 0, ((300, 200), (500, 300), 0))])
    monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **k: guide)
    monkeypatch.setattr(multi_zone, '_inner_rows', lambda *a: [{}, {}, {}])
    plan = {'method': 'ellipse_detected_rows'}
    monkeypatch.setattr(detected_layout, 'prepare_detected_layout', lambda *a, **k: plan)
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0,
                                          reader=object(), provider='paddle_v6') is plan


def test_sparse_optional_lower_residue_does_not_cancel_complete_upper_arc():
    image = np.full((600, 800, 3), 255, np.uint8)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx-400)/350, (yy-300)/250)
    theta = np.linspace(0, 2*np.pi, 720, endpoint=False)
    guide = SimpleNamespace(annulus=[(0, 0, ((400, 300), (700, 500), 0))],
                            points=np.column_stack([400+350*np.cos(theta),
                                                    300+250*np.sin(theta)]))
    for angle in [185, 215, 245, 275, 305, 335, 355, 60]:
        t = np.deg2rad(angle)
        x, y = round(400+245*np.cos(t)), round(300+175*np.sin(t))
        cv2.rectangle(image, (x-9, y-18), (x+9, y+18), (40, 40, 255), -1)
    assert multi_zone._arc_maps(image, rho, guide, allow_empty_lower=True) is None
    maps = multi_zone._arc_maps(image, rho, guide, allow_empty_lower=True,
                                allow_sparse_lower=True)
    assert set(maps) == {'upper'}
