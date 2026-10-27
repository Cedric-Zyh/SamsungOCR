"""Faint table rescue must be geometric, scoped, and faithful to source pixels."""
import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.pale_seal import enhance_pale_red, pale_red_statistics
from receipt_ocr.recognition.seal.preprocess import inputs, pale_grid, receiving_grid


def pale_form(*, ink=(230, 230, 245), missing=None):
    image = np.full((410, 650, 3), 255, np.uint8)
    for y in (40, 120, 200, 280, 360):
        cv2.line(image, (40, y), (610, y), ink, 4)
    for x in (40, 610):
        cv2.line(image, (x, 40), (x, 360), ink, 4)
    for row in range(4):
        for x in (90, 150, 210, 400, 460):
            cv2.rectangle(image, (x, 65+row*80), (x+18, 90+row*80), ink, 2)
    image[70:85, 270:295] = 20
    if missing is not None:
        y = {'internal': 200, 'top': 40, 'bottom': 360}[missing]
        image[y-5:y+6, 46:103] = 255
    return image


def test_only_pale_observed_red_ink_qualifies():
    assert pale_red_statistics(pale_form()) is not None
    assert pale_red_statistics(pale_form(ink=(245, 230, 245))) is not None
    assert pale_red_statistics(pale_form(ink=(30, 30, 230))) is None
    assert pale_red_statistics(pale_form(ink=(245, 230, 230))) is None


@pytest.mark.parametrize('image', [None, np.zeros((20, 20, 3), np.uint8),
                                    np.zeros((100, 100), np.uint8),
                                    np.full((100, 100, 3), 255, np.uint8)])
def test_missing_or_unsupported_images_do_not_qualify(image):
    assert pale_red_statistics(image) is None


def test_continuous_weak_strokes_are_retained_without_growing_ink_or_black_writing():
    image = np.full((60, 120, 3), 255, np.uint8)
    for index, difference in enumerate((2, 4, 12, 40)):
        image[10:20, 5+index*20:10+index*20] = (255-difference, 255-difference, 255)
    image[35:45, 5:15] = (40, 40, 40)
    image[35:45, 35:45] = (40, 40, 43)
    before = image.copy()
    enhanced = enhance_pale_red(image)
    assert np.array_equal(image, before)
    for index, difference in enumerate((2, 4, 12, 40)):
        assert enhanced[15, 7+index*20, 1] == 255-min(235, difference*6)
    assert np.all(enhanced[35:45, 5:15] == 255)
    assert np.all(enhanced[35:45, 35:45] == 255)
    assert np.all(enhanced[np.all(image == 255, axis=2)] == 255)


def test_pink_ink_is_retained_when_red_and_blue_channels_are_equal():
    image = np.full((60, 120, 3), 255, np.uint8)
    for index, difference in enumerate((2, 4, 12, 40)):
        image[10:20, 5+index*20:10+index*20] = (255, 255-difference, 255)
    image[35:45, 5:15] = (245, 220, 220)  # Blue is not red/pink evidence.
    before = image.copy()
    enhanced = enhance_pale_red(image)
    for index, difference in enumerate((2, 4, 12, 40)):
        assert enhanced[15, 7+index*20, 1] == 255-min(235, difference*6)
    assert np.all(enhanced[35:45, 5:15] == 255)
    assert np.array_equal(image, before)


@pytest.mark.parametrize('missing', ['internal', 'top', 'bottom'])
def test_only_internal_dividers_can_use_bounded_missing_end(missing):
    measured = receiving_grid.measure_receiving_grid(pale_form(missing=missing))
    if missing == 'internal':
        assert measured['accepted'] and len(measured['row_boxes']) == 4
    else:
        assert not measured['accepted']


def test_previously_supported_dividers_keep_the_original_acceptance_gate(monkeypatch):
    mask = np.zeros((410, 650), np.uint8)
    sides = [{'x': 40, 'top': 40, 'bottom': 360}, {'x': 610, 'top': 40, 'bottom': 360}]
    # Both ends meet their side; a gap in the middle leaves ~83% support.
    # The stronger rule for recovering a missing end must not reject this.
    monkeypatch.setattr(receiving_grid, '_hough', lambda *a: [
        [40, 200, 250, 200], [350, 200, 610, 200]])
    levels, _ = receiving_grid._spanning_rules(mask, sides)
    assert len(levels) == 1 and .8 < levels[0]['support_fraction'] < .85


def test_short_collinear_fragments_recover_dividers_without_altering_source(monkeypatch):
    image = pale_form()
    for y in (120, 200, 280):
        image[y-5:y+6, 46:605] = 255
        for left, right in ((40, 203), (243, 406), (446, 610)):
            cv2.line(image, (left, y), (right, y), (230, 230, 245), 4)
    before = image.copy()
    with monkeypatch.context() as m:
        m.setattr(receiving_grid, '_fragmented_internal_rules', lambda *a: [])
        assert not receiving_grid.measure_receiving_grid(image)['accepted']
    measured = receiving_grid.measure_receiving_grid(image)
    assert measured['accepted'] and len(measured['row_boxes']) == 4
    assert measured['metrics']['fragmented_rule_recovery']
    assert np.array_equal(image, before)
    assert all(level['support_fraction'] >= .8 for level in measured['metrics']['levels'])


def test_fragmented_rule_requires_observed_ink_not_only_fitted_segments(monkeypatch):
    mask = np.zeros((410, 650), np.uint8)
    sides = [{'x': 40, 'top': 40, 'bottom': 360}, {'x': 610, 'top': 40, 'bottom': 360}]
    monkeypatch.setattr(receiving_grid, '_hough', lambda *a: [
        [40, 200, 210, 200], [240, 200, 410, 200], [440, 200, 610, 200]])
    assert receiving_grid._fragmented_internal_rules(mask, sides) == []


def test_faint_oval_with_internal_lines_does_not_create_a_table(tmp_path):
    image = np.full((410, 650, 3), 255, np.uint8)
    cv2.ellipse(image, (325, 205), (280, 160), 0, 0, 360, (230, 230, 245), 4)
    for y in (130, 200, 270):
        cv2.line(image, (150, y), (500, y), (230, 230, 245), 3)
    source = tmp_path/'oval.png'
    cv2.imwrite(str(source), image)
    audit = {}
    assert pale_grid.prepare_pale_grid(source, tmp_path/'out', 0, '/files', 'combined', audit=audit) is None
    assert audit['status'] == 'fallback' and audit['reason']
    assert not (tmp_path/'out').exists()


def test_pale_gate_skips_grid_measurement_and_output_for_strong_ink(tmp_path, monkeypatch):
    source = tmp_path/'strong.png'
    cv2.imwrite(str(source), pale_form(ink=(30, 30, 230)))
    def unexpected(*a):
        raise AssertionError('ordinary strong stamps must not run the rescue measurement')
    monkeypatch.setattr(pale_grid, 'measure_receiving_grid', unexpected)
    assert pale_grid.prepare_pale_grid(source, tmp_path/'out', 0, '/files', 'combined') is None
    assert pale_grid.prepare_pale_grid(tmp_path/'absent.png', tmp_path/'out', 0, '', 'combined', color='blue') is None
    assert not (tmp_path/'out').exists()


def test_prepared_rows_are_exact_crops_of_the_displayed_enhanced_image(tmp_path):
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), pale_form(missing='internal'))
    before = source.read_bytes()
    metadata, prepared, jobs = pale_grid.prepare_pale_grid(source, tmp_path/'out', 0, '/files', 'combined')
    enhanced = cv2.imread(str(tmp_path/'out'/metadata['body_url'].rsplit('/', 1)[-1]))
    reference = cv2.imread(str(tmp_path/'out'/metadata['pale_grid_enhancement']['reference_url'].rsplit('/', 1)[-1]))
    assert enhanced.shape == reference.shape
    assert reference.min() < 30  # Original handwriting remains auditable.
    pixels = reference.astype(np.int16)
    red = pixels[:, :, 2]-np.maximum(pixels[:, :, 0], pixels[:, :, 1]) > 8
    assert np.median(enhanced[:, :, 1][red]) < np.median(reference[:, :, 1][red])
    assert metadata['shape_code'] == 'rectangle' and len(jobs) == 4
    for row, (item, path) in zip(metadata['rectangular_fixed_rows'], jobs):
        l, t, r, b = row['crop_box']
        actual = cv2.imread(str(path))
        assert np.array_equal(actual[20:-20, 20:-20], enhanced[t:b, l:r])
        assert np.all(actual[:20] == 255)
        assert metadata['rectangular_row_urls'][item['rectangle_row_index']] == item['image_url']
        assert item['channel'] == 'body' and item['read_mode'] == 'line'
    assert source.read_bytes() == before


def test_pipeline_rescues_wrong_ellipse_route_before_any_round_orientation(tmp_path, monkeypatch):
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), pale_form())
    monkeypatch.setattr(inputs, 'save_region_crop', lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, 'save_color_isolated_seal', lambda s, d, r: shutil.copyfile(s, d))
    monkeypatch.setattr(inputs, 'classify_shape', lambda *a: 'ellipse')
    def unexpected(*a, **k):
        raise AssertionError('a verified table must not enter circular orientation/OCR')
    monkeypatch.setattr(inputs, 'prepare_round_stamp_combined', unexpected)
    monkeypatch.setattr(inputs, 'OcrReader', unexpected)
    artifact, jobs = inputs.prepare_inputs(source, SealRegion(0, 0, 1, 1, 'red', '', 0), 0,
                                          tmp_path/'out', '/files', 'paddle_v6', 'combined')
    assert artifact['errors'] == []
    assert artifact['shape_before_pale_grid'] == '椭圆'
    assert artifact['shape_code'] == 'rectangle' and len(jobs) == 4
    assert artifact['pale_grid_enhancement']['gain'] == 6
    assert 'ring_input_url' not in artifact and 'unwrapped_url' not in artifact
