from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.imaging.ellipse import map_ellipse_box_to_normalized, save_ellipse_normalized_seal
from receipt_ocr.recognition.seal.preprocess.inputs import (
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


def test_center_and_number_use_final_coordinates_without_a_public_artifact_url(tmp_path, monkeypatch):
    from PIL import Image
    from receipt_ocr.imaging.contracts import SealRegion
    from receipt_ocr.recognition.seal.preprocess import inputs

    source = tmp_path / 'stamp.png'
    Image.new('RGB', (400, 400), 'white').save(source)
    monkeypatch.setattr(inputs, 'save_region_crop', lambda source, target, region: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'save_color_isolated_seal', lambda source, target, region: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *_: 'round')
    final_box = [100, 150, 300, 200]

    def orient(source, target, **kwargs):
        Image.open(source).save(target)
        return target, {'oriented_type_row_exact_box': final_box, 'type_row_exact_box': [0, 0, 10, 10]}

    def number(source, destination, focus, **kwargs):
        assert focus == final_box
        Image.new('RGB', (60, 50), 'white').save(destination)
        return {'status': 'detected', 'text': '(1', 'confidence': .95}

    monkeypatch.setattr(inputs, 'prepare_round_stamp_combined', orient)
    monkeypatch.setattr(inputs, 'prepare_number_input', number)
    monkeypatch.setattr(inputs, 'save_unwrapped_seal', lambda source, target, *a, **kw: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'save_ring_text_band_preview', lambda source, target, **kw: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'save_company_ring_input', lambda *a: None)
    artifact, jobs = inputs.prepare_inputs(
        source, SealRegion(0, 0, 1, 1, 'red', '客户章', 0), 0,
        tmp_path / 'artifacts', '', 'paddle_v6', 'combined',
    )
    assert artifact['errors'] == []
    assert artifact['ring_input_box'] == final_box
    assert [item['channel'] for item, _ in jobs] == ['center', 'number', 'ring']
    with Image.open(jobs[0][1]) as center:
        assert center.size == (200, 50)


def test_text_mask_preserves_crossing_border_and_adjacent_faint_ink(tmp_path):
    source, target = tmp_path / 'source.png', tmp_path / 'ring.png'
    mask_path = tmp_path / 'mask.png'
    image = np.full((120, 140, 3), 255, dtype=np.uint8)
    cv2.circle(image, (70, 60), 50, (10, 10, 245), 3)
    image[70:80, 55:65] = (252, 252, 255)  # faint type ink inside the polygon
    image[70:80, 95:100] = (252, 252, 255)  # neighbour immediately outside
    image[45:55, 60:70] = (10, 10, 245)  # separate number/star outside
    cv2.imwrite(str(source), image)
    polygon = [[40, 65], [94, 65], [94, 115], [40, 115]]  # overlaps lower rim
    _white_detected_type_region(source, target, polygons=[polygon], mask_destination=mask_path)
    actual = cv2.imread(str(target))
    expected = image.copy()
    expected[70:80, 55:65] = 255
    assert np.array_equal(actual, expected)
    mask = cv2.imread(str(mask_path), 0)
    assert np.array_equal(mask > 0, np.any(expected != image, axis=2))


def test_partial_type_polygon_never_masks_its_mirror(tmp_path, monkeypatch):
    from receipt_ocr.imaging.contracts import SealRegion
    from receipt_ocr.recognition.seal.preprocess import inputs
    from PIL import Image
    source = tmp_path / 'source.png'
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    image[50:65, 30:55] = (20, 20, 240)
    image[50:65, 105:130] = (20, 20, 240)
    cv2.imwrite(str(source), image)
    monkeypatch.setattr(inputs, 'save_region_crop', lambda source, target, region: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'save_color_isolated_seal', lambda source, target, region: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *_: 'round')
    monkeypatch.setattr(inputs, 'prepare_round_stamp_combined', lambda *a, **k: (None, {
        'partial_anchor': True, 'oriented_type_row_exact_box': [25, 45, 60, 70],
        'oriented_type_row_polygons': [[[25, 45], [60, 45], [60, 70], [25, 70]]],
    }))
    monkeypatch.setattr(inputs, 'prepare_number_input', lambda *a, **k: {'status': 'not_detected'})
    monkeypatch.setattr(inputs, 'recover_number_punctuation', lambda *a, **k: ({}, {'status': 'not_needed'}))
    observed = []
    def unwrap(path, target, *a, **kw):
        observed.append(cv2.imread(str(path)))
        Image.open(path).save(target)
    monkeypatch.setattr(inputs, 'save_unwrapped_seal', unwrap)
    monkeypatch.setattr(inputs, 'save_ring_text_band_preview', lambda source, target, **kw: Image.open(source).save(target))
    monkeypatch.setattr(inputs, 'save_company_ring_input', lambda *a: None)
    artifact, jobs = inputs.prepare_inputs(source, SealRegion(0, 0, 1, 1, 'red', '客户章', 0), 0,
                                          tmp_path / 'out', '/files', 'paddle_v6', 'combined')
    assert not artifact['errors']
    expected = image.copy(); expected[50:65, 30:55] = 255
    assert len(observed) == 1 and np.array_equal(observed[0], expected)
    assert artifact['ring_input_mask_method'] == 'contained_text_ink'
    assert [i['channel'] for i, _ in jobs] == ['center', 'ring']


def test_pale_interpolation_bridge_does_not_keep_the_entire_type_row(tmp_path):
    source, target = tmp_path / 'source.png', tmp_path / 'ring.png'
    image = np.full((80, 120, 3), 255, dtype=np.uint8)
    image[30:40, 25:50] = (20, 20, 240)
    image[30:40, 90:105] = (20, 20, 240)
    image[35, 50:90] = (253, 253, 255)  # pale bridge to a neighbouring glyph
    cv2.imwrite(str(source), image)
    _white_detected_type_region(source, target, polygons=[[[20, 25], [55, 25], [55, 45], [20, 45]]])
    result = cv2.imread(str(target))
    assert np.all(result[30:40, 25:50] == 255)
    assert np.array_equal(result[:, 56:], image[:, 56:])
