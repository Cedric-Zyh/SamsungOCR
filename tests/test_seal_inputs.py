from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ellipse import map_ellipse_box_to_normalized, save_ellipse_normalized_seal
from receipt_ocr.recognition.seal.preprocess.inputs import (
    _symmetric_type_geometry,
    _white_detected_type_region,
)
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _oriented_type_row_exact_box


def test_ring_input_is_saved_with_type_band_filled_white(tmp_path):
    source = Path(tmp_path) / "oriented.png"
    destination = Path(tmp_path) / "ring-input.png"
    image = np.full((40, 80, 3), 255, dtype=np.uint8)
    image[8:16, 10:70] = (0, 0, 255)
    image[24:30, 10:70] = (0, 0, 255)
    cv2.imwrite(str(source), image)

    _white_detected_type_region(source, destination, (8, 20, 72, 34))

    result = cv2.imread(str(destination), cv2.IMREAD_COLOR)
    assert tuple(result[26, 30]) == (255, 255, 255)
    assert tuple(result[10, 30]) == (0, 0, 255)


def test_mask_box_uses_the_ocr_polygon_without_padding():
    points = [[10, 20], [50, 20], [50, 40], [10, 40]]
    box = _oriented_type_row_exact_box(
        [{"text": "业务专用章", "points": points, "angle": 0}],
        {"anchor_text": "业务专用章", "anchor_points": points, "angle": 0},
        np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        100,
        100,
    )
    assert box == [10, 20, 50, 40]


def test_symmetric_type_geometry_mirrors_after_rotation_frame(tmp_path):
    source = tmp_path / "oriented.png"
    cv2.imwrite(str(source), np.full((100, 100, 3), 255, dtype=np.uint8))
    geometry = _symmetric_type_geometry(
        source,
        {
            "oriented_type_row_polygons": [
                [[10, 20], [30, 20], [30, 40], [10, 40]]
            ]
        },
    )
    assert geometry["center_x"] == 50.0
    assert geometry["focus_box"] == [10, 20, 90, 40]
    assert geometry["mask_polygons"][1] == [[90, 20], [70, 20], [70, 40], [90, 40]]


def test_polygon_mask_keeps_unrelated_ring_pixels(tmp_path):
    source = tmp_path / "oriented.png"
    destination = tmp_path / "ring-input.png"
    image = np.full((80, 100, 3), 255, dtype=np.uint8)
    image[20:40, 10:30] = (0, 0, 255)
    image[20:40, 70:90] = (0, 0, 255)
    image[20:40, 45:55] = (0, 255, 0)
    cv2.imwrite(str(source), image)

    _white_detected_type_region(
        source,
        destination,
        polygons=[
            [[10, 20], [30, 20], [30, 40], [10, 40]],
            [[90, 20], [70, 20], [70, 40], [90, 40]],
        ],
    )
    result = cv2.imread(str(destination), cv2.IMREAD_COLOR)
    assert tuple(result[30, 20]) == (255, 255, 255)
    assert tuple(result[30, 80]) == (255, 255, 255)
    assert tuple(result[30, 50]) == (0, 255, 0)


def test_ellipse_detector_box_is_mapped_into_normalized_coordinates(tmp_path):
    source = Path(tmp_path) / "ellipse.png"
    normalized = Path(tmp_path) / "ellipse-normalized.png"
    image = np.full((100, 200, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (100, 50), (78, 30), 0, 0, 360, (0, 0, 220), 4)
    assert cv2.imwrite(str(source), image)

    save_ellipse_normalized_seal(source, normalized, color="red")
    output = cv2.imread(str(normalized))
    box = map_ellipse_box_to_normalized(source, (70, 40, 130, 60), color="red")

    assert output is not None
    assert output.shape[0] == output.shape[1]
    assert box is not None
    assert 0 <= box[0] < box[2] <= output.shape[1]
    assert 0 <= box[1] < box[3] <= output.shape[0]
    assert box != [70, 40, 130, 60]


def test_ellipse_pipeline_keeps_original_aspect_through_ring_input(tmp_path, monkeypatch):
    from receipt_ocr.imaging.contracts import SealRegion
    from receipt_ocr.recognition.seal.preprocess import inputs
    source = tmp_path / 'oval.png'
    image = np.full((400, 800, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (400, 200), (340, 150), 0, 0, 360, (50, 50, 220), 8)
    cv2.putText(image, 'COMPANY', (250, 110), cv2.FONT_HERSHEY_SIMPLEX, 1, (50, 50, 220), 3)
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(inputs, 'classify_shape', lambda *_: 'ellipse')
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, 'red', '客户章', 0), 0,
        tmp_path / 'artifacts', '/files/test', 'paddle_v6', 'none',
    )
    assert not artifact['errors']
    assert artifact['ring_geometry'] == 'ellipse_observed_border'
    assert artifact['ring_border_detection']['supported_fraction'] > .85
    assert artifact['ring_border_url'].endswith('seal-0-ring-border.png')
    assert 'ellipse_normalized_url' not in artifact
    assert all(item['name'] != 'normalized' for item in artifact['inputs'])
    color = cv2.imread(str(tmp_path / 'artifacts/seal-0-color.png'))
    ring_input = cv2.imread(str(tmp_path / 'artifacts/seal-0-ring-input.png'))
    assert ring_input.shape == color.shape
    assert ring_input.shape[1] == ring_input.shape[0] * 2
    assert np.array_equal(color, ring_input)
    assert [item['channel'] for item, _ in jobs] == ['ring']
