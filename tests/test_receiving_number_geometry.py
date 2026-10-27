"""Right-side number inputs come from complete, isolated circular red pixels."""
from inspect import signature

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.receiving_number import prepare_receiving_number


RED = (25, 35, 225)


def row_image(kind='circled', *, width=960, height=180, circle_x=770, circle_y=88,
              radius=42, rules=True, color=RED, digit='2'):
    image = np.full((height, width, 3), 255, np.uint8)
    if rules:
        cv2.line(image, (18, 20), (width-18, 20), color, 6)
        cv2.line(image, (18, height-22), (width-18, height-22), color, 6)
    # Five independent glyph-shaped ink groups establish the real purpose
    # body's spacing and height without any OCR string or expected number.
    for x in [100, 182, 264, 346, 428]:
        cv2.rectangle(image, (x, 46), (x+60, 126), color, 5)
        cv2.line(image, (x+4, 86), (x+56, 86), color, 5)
    if kind in {'circled', 'double', 'hollow', 'disc', 'square', 'oval'}:
        if kind == 'square':
            cv2.rectangle(image, (circle_x-radius, circle_y-radius),
                          (circle_x+radius, circle_y+radius), color, 5)
        elif kind == 'oval':
            cv2.ellipse(image, (circle_x, circle_y), (radius+17, radius-9), 0, 0, 360, color, 5)
        else:
            cv2.circle(image, (circle_x, circle_y), radius, color, -1 if kind == 'disc' else 5)
        if kind not in {'hollow', 'disc'}:
            cv2.putText(image, digit, (circle_x-19, circle_y+20), cv2.FONT_HERSHEY_SIMPLEX,
                        1.7, color, 5, cv2.LINE_AA)
        if kind == 'double':
            cv2.circle(image, (circle_x-115, circle_y), radius, color, 5)
            cv2.putText(image, '3', (circle_x-134, circle_y+20), cv2.FONT_HERSHEY_SIMPLEX,
                        1.7, color, 5, cv2.LINE_AA)
    elif kind == 'digit':
        cv2.putText(image, '2', (circle_x-20, circle_y+27), cv2.FONT_HERSHEY_SIMPLEX,
                    2.5, color, 7, cv2.LINE_AA)
    elif kind == 'date':
        cv2.putText(image, '2026', (circle_x-120, circle_y+27), cv2.FONT_HERSHEY_SIMPLEX,
                    2, color, 6, cv2.LINE_AA)
    elif kind == 'rule_noise':
        cv2.line(image, (circle_x-50, circle_y), (width-12, circle_y), color, 7)
    return image


def prepare(tmp_path, image, *, purpose_right=None, index=1, row_index=1):
    source = tmp_path / 'row.png'
    assert cv2.imwrite(str(source), image)
    metadata, jobs = prepare_receiving_number(source, tmp_path / 'out', index, '/files/test',
                                              row_index=row_index, purpose_right=purpose_right)
    return metadata, jobs, source


@pytest.mark.parametrize('purpose_right', [None, 600])
def test_only_real_component_plus_margin_enters_two_fixed_color_inputs(tmp_path, purpose_right):
    image = row_image()
    metadata, jobs, source = prepare(tmp_path, image, purpose_right=purpose_right)
    assert metadata['prepared'] is True
    assert len(jobs) == len(metadata['inputs']) == 2
    assert metadata['source_size'] == [960, 180]
    assert metadata['row_index'] == 1
    assert metadata['purpose_right_source'] == ('detected_box' if purpose_right else 'contiguous_ink_group')
    candidate = metadata['candidates'][0]
    assert candidate['circle']['accepted'] is True
    assert candidate['circle']['outer_angular_coverage'] >= .72
    assert .04 <= candidate['circle']['inner_ink_fraction'] < .98
    bx0, by0, bx1, by1 = candidate['box']
    assert metadata['crop_box'] == [bx0-8, by0-8, bx1+8, by1+8]
    x0, y0, x1, y1 = metadata['crop_box']
    crop = image[y0:y1, x0:x1]
    for item, path in jobs:
        pixels = cv2.imread(str(path))
        expected = crop if item['number_scale'] == 1 else cv2.resize(
            crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        np.testing.assert_array_equal(pixels[16:-16, 16:-16], expected)
        assert np.all(pixels[:16] == 255) and np.all(pixels[-16:] == 255)
        assert np.all(pixels[:, :16] == 255) and np.all(pixels[:, -16:] == 255)
        assert item['blank_padding'] == 16 and item['representation'] == 'color'
        assert item['channel'] == 'receiving_number' and item['matching'] is False
        assert item['rectangle_row_index'] == 1 and item['crop_box'] == metadata['crop_box']
        assert item['image_url'].endswith(path.name)
    np.testing.assert_array_equal(cv2.imread(str(source)), image)


@pytest.mark.parametrize('scale', [.65, 1.25])
def test_geometry_tracks_actual_row_scale_not_sample_coordinates(tmp_path, scale):
    image = cv2.resize(row_image(), None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    metadata, jobs, _ = prepare(tmp_path, image)
    assert metadata['prepared'] is True and len(jobs) == 2
    x0, _, x1, _ = metadata['candidates'][0]['box']
    assert x0 < 770*scale < x1
    assert metadata['glyph_height'] > 40*scale


def test_extra_left_whitespace_does_not_turn_the_search_into_a_fixed_x_fraction(tmp_path):
    image = cv2.copyMakeBorder(row_image(), 0, 0, 600, 0, cv2.BORDER_CONSTANT,
                                value=(255, 255, 255))
    metadata, jobs, _ = prepare(tmp_path, image)
    assert metadata['prepared'] and len(jobs) == 2
    assert metadata['candidates'][0]['box'][0] > 1300


@pytest.mark.parametrize('digit', ['1', '3', '9'])
def test_geometry_does_not_prefer_the_number_shown_in_the_reported_sample(tmp_path, digit):
    metadata, jobs, _ = prepare(tmp_path, row_image(digit=digit))
    assert metadata['prepared'] and len(jobs) == 2
    assert all('text' not in item for item, _ in jobs)
    assert 'number' not in metadata


@pytest.mark.parametrize('kind', ['none', 'digit', 'date', 'double', 'hollow', 'disc',
                                 'square', 'oval', 'rule_noise'])
def test_absent_plain_numeric_multiple_or_non_circular_marks_are_not_proposed(tmp_path, kind):
    metadata, jobs, source = prepare(tmp_path, row_image(kind))
    assert not metadata['prepared'] and jobs == []
    assert source.exists() and not (tmp_path / 'out').exists()


@pytest.mark.parametrize('circle_x,circle_y,radius', [(525, 88, 42), (770, 128, 42),
                                                    (770, 28, 42), (928, 88, 42),
                                                    (770, 88, 17)])
def test_touching_body_other_row_clipping_and_undersized_marks_keep_original_row(tmp_path, circle_x, circle_y, radius):
    metadata, jobs, _ = prepare(tmp_path, row_image(circle_x=circle_x, circle_y=circle_y, radius=radius))
    assert not metadata['prepared'] and not jobs
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('purpose_right', [True, False, float('nan'), float('inf'),
                                         -1, 0, 960, 1000, 'invalid', []])
def test_invalid_detected_purpose_edges_are_rejected_without_outputs(tmp_path, purpose_right):
    metadata, jobs, _ = prepare(tmp_path, row_image(), purpose_right=purpose_right)
    assert metadata['prepared'] is False and metadata['reason'] == 'invalid_purpose_right'
    assert not jobs and not (tmp_path / 'out').exists()


@pytest.mark.parametrize('purpose_right', [100, 200, 900])
def test_wrong_but_finite_purpose_edge_cannot_select_an_arbitrary_circle(tmp_path, purpose_right):
    metadata, jobs, _ = prepare(tmp_path, row_image(), purpose_right=purpose_right)
    assert not metadata['prepared'] and not jobs


def test_blue_or_neutral_numbers_are_not_red_stamp_number_evidence(tmp_path):
    metadata, jobs, _ = prepare(tmp_path, row_image(color=(220, 40, 30)))
    assert not metadata['prepared'] and not jobs
    metadata, jobs, _ = prepare(tmp_path, row_image(color=(30, 30, 30)))
    assert not metadata['prepared'] and not jobs


def test_a_circle_without_a_complete_purpose_group_is_not_a_receiving_number(tmp_path):
    image = row_image()
    image[:, :600] = 255
    metadata, jobs, _ = prepare(tmp_path, image)
    assert not metadata['prepared'] and metadata['reason'] == 'no_complete_purpose_ink_group'
    assert not jobs


def test_unreadable_or_unsupported_rows_produce_no_files(tmp_path):
    metadata, jobs = prepare_receiving_number(tmp_path/'missing.png', tmp_path/'out', 0,
                                               '/files/test', row_index=1)
    assert not metadata['prepared'] and metadata['reason'] == 'unreadable_row_image'
    assert not jobs and not (tmp_path/'out').exists()
    metadata, jobs, _ = prepare(tmp_path, np.full((20, 300, 3), 255, np.uint8))
    assert not metadata['prepared'] and metadata['reason'] == 'unsupported_row_geometry'
    assert not jobs


@pytest.mark.parametrize('index,row_index', [(True, 1), (1, False), (-1, 1), (1, -1),
                                            (1.0, 1), (1, '1')])
def test_invalid_region_and_physical_row_indexes_never_read_or_write(tmp_path, index, row_index):
    metadata, jobs = prepare_receiving_number(tmp_path/'missing.png', tmp_path/'out', index,
                                               '/files/test', row_index=row_index)
    assert not metadata['prepared'] and metadata['reason'] == 'invalid_region_or_row_index'
    assert not jobs and not (tmp_path/'out').exists()


def test_image_write_failure_is_reported_instead_of_returning_unreadable_input(tmp_path, monkeypatch):
    source = tmp_path/'row.png'
    assert cv2.imwrite(str(source), row_image())
    monkeypatch.setattr(cv2, 'imwrite', lambda *args: False)
    with pytest.raises(ValueError, match='圈号输入保存失败'):
        prepare_receiving_number(source, tmp_path/'out', 1, '/files/test', row_index=1)


def test_contract_contains_no_ocr_or_requested_number_parameter():
    assert list(signature(prepare_receiving_number).parameters) == [
        'row_image_path', 'directory', 'index', 'prefix', 'row_index', 'purpose_right']
