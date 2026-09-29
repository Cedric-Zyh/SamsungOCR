from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.processing import (
    map_ellipse_box_to_normalized,
    save_ellipse_normalized_seal,
)
from receipt_ocr.recognition.seal.preprocess.inputs import (
    _symmetric_type_geometry,
    _white_detected_type_region,
)
from receipt_ocr.recognition.seal.preprocess.orientation import _oriented_type_row_exact_box


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
