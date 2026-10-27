import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import receiving_grid


def grid_image():
    image = np.full((410, 650, 3), 255, np.uint8)
    for y in [40, 120, 200, 280, 360]:
        cv2.line(image, (40, y), (610, y), (30, 30, 220), 4)
    for x in [40, 610]:
        cv2.line(image, (x, 40), (x, 360), (30, 30, 220), 4)
    # Short, disconnected strokes are content, not a replacement grid rule.
    for row in range(4):
        for x in [90, 150, 210, 400, 460]:
            cv2.rectangle(image, (x, 65+row*80), (x+18, 90+row*80), (80, 80, 210), 2)
    image[70:85, 270:295] = 15  # Actual black writing remains in the colour input.
    return image


def test_complete_measured_cells_retain_first_row_and_original_pixels():
    image = grid_image()
    original = image.copy()
    result = receiving_grid.measure_receiving_grid(image)
    assert result['accepted']
    assert len(result['row_boxes']) == 4
    assert np.array_equal(image, original)
    assert np.min(result['image']) < 20
    height, width = result['image'].shape[:2]
    left, top, right, bottom = result['frame_box']
    assert 0 <= left < right <= width and 0 <= top < bottom <= height
    rows = result['row_boxes']
    assert rows[0][1] < rows[0][3] < rows[1][1] < rows[-1][3] <= bottom
    assert all(row[0] == left and row[2] == right for row in rows)
    transform = np.asarray(result['source_to_rotated_matrix'])
    assert transform.shape == (2, 3) and np.isfinite(transform).all()
    point = np.asarray([[[280, 77]]], np.float32)
    x, y = cv2.transform(point, transform)[0, 0].round().astype(int)
    assert result['image'][y, x].max() < 30


def test_small_tilt_is_corrected_from_lines_without_changing_cell_count():
    image = cv2.copyMakeBorder(grid_image(), 80, 80, 80, 80, cv2.BORDER_CONSTANT,
                               value=(255, 255, 255))
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width/2, height/2), 3, 1)
    tilted = cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))
    result = receiving_grid.measure_receiving_grid(tilted)
    assert result['accepted'] and len(result['row_boxes']) == 4
    assert result['angle'] == pytest.approx(-3, abs=.4)


@pytest.mark.parametrize('edge', ['top', 'bottom'])
def test_internal_three_cells_cannot_replace_missing_outer_rule(edge):
    image = grid_image()
    y = 40 if edge == 'top' else 360
    image[y-5:y+6, 46:605] = 255
    result = receiving_grid.measure_receiving_grid(image)
    assert not result['accepted']
    assert result['reason'] == 'incomplete_outer_grid_rules'


@pytest.mark.parametrize('axes', [(280, 160), (170, 170)])
def test_true_oval_and_round_with_inner_horizontal_strokes_are_not_grids(axes):
    image = np.full((410, 650, 3), 255, np.uint8)
    cv2.ellipse(image, (325, 205), axes, 0, 0, 360, (0, 0, 255), 5)
    for y in [150, 200, 250]:
        cv2.line(image, (150, y), (500, y), (0, 0, 255), 3)
    assert not receiving_grid.measure_receiving_grid(image)['accepted']


def test_missing_side_and_blue_grid_do_not_supply_red_grid_evidence():
    image = grid_image()
    image[35:366, 35:46] = 255
    assert not receiving_grid.measure_receiving_grid(image)['accepted']
    assert not receiving_grid.measure_receiving_grid(grid_image()[:, :, ::-1].copy())['accepted']


@pytest.mark.parametrize('image', [
    None, np.zeros((10, 100, 3), np.uint8), np.zeros((100, 100), np.uint8),
    np.zeros((100, 100, 4), np.uint8), np.zeros((0, 100, 3), np.uint8),
    np.full((100, 100, 3), np.nan), np.zeros((100, 100, 3), dtype=object),
    np.zeros((40, 2000, 3), np.uint8),
])
def test_invalid_or_nonfinite_images_reject_without_preparation(image):
    result = receiving_grid.measure_receiving_grid(image)
    assert result['accepted'] is False
    assert 'image' not in result and 'row_boxes' not in result


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_nonfinite_measured_angle_is_not_used(monkeypatch, value):
    monkeypatch.setattr(receiving_grid, '_rotation_angle', lambda mask: value)
    assert not receiving_grid.measure_receiving_grid(grid_image())['accepted']


def test_nonfinite_side_extent_is_not_used(monkeypatch):
    side = {'x': float('nan'), 'top': 40, 'bottom': 360, 'coverage': 1.0}
    other = dict(side, x=600)
    monkeypatch.setattr(receiving_grid, '_paired_sides', lambda mask: ((side, other), []))
    result = receiving_grid.measure_receiving_grid(grid_image())
    assert not result['accepted'] and result['reason'] == 'invalid_measured_side_extent'


def test_prepared_input_coordinates_padding_and_colour_preservation(tmp_path):
    source = tmp_path / 'source.png'
    cv2.imwrite(str(source), grid_image())
    measured = receiving_grid.measure_receiving_grid(grid_image())
    result = receiving_grid.prepare_receiving_grid(source, tmp_path / 'stage', 7, '/files/evidence')
    assert result['accepted'] and len(result['rows']) == 4
    whole = cv2.imread(result['whole_color_path'])
    left, top, right, bottom = result['frame_box']
    assert whole.shape[:2] == (bottom-top, right-left)
    assert result['whole_color_url'].startswith('/files/evidence/seal-7-receiving-grid-')
    for row in result['rows']:
        l, t, r, b = row['image_box']
        assert row['box'] == [l-left, t-top, r-left, b-top]
        color = cv2.imread(row['color_path'])
        gray = cv2.imread(row['gray_path'], cv2.IMREAD_GRAYSCALE)
        assert color.shape[:2] == gray.shape == (b-t+16, r-l+16)
        assert np.all(color[:8] == 255) and np.all(gray[:8] == 255)
        assert np.array_equal(color[8:-8, 8:-8], measured['image'][t:b, l:r])
        assert row['padding'] == 8
    assert whole.min() < 20
