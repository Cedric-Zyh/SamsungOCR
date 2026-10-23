"""Reading margins must not reject or erase separate interior glyph rows."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import multi_zone, detected_layout


def layout(monkeypatch, tmp_path, *, fitted=False, scale=1):
    image = np.full((600, 800, 3), 255, np.uint8)
    rows = [
        {'box': [375, 240, 425, 268], 'read_box': [367, 232, 433, 276]},
        {'box': [260, 274, 540, 324], 'read_box': [252, 266, 548, 332]},
        {'box': [290, 350, 510, 390], 'read_box': [282, 342, 518, 398]},
    ]
    for row in rows:
        l, t, r, b = row['box']
        image[t:b, l:r] = (70, 70, 245)
    # Same-height side letters, rim, and faint ink in reading margins survive.
    cv2.ellipse(image, (400, 300), (350, 250), 0, 0, 360, (70, 70, 245), 3)
    image[280:310, 100:120] = (70, 70, 245)
    image[270:273, 380:420] = (238, 238, 250)
    image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    rows = [{key: [round(v*scale) for v in row[key]] for key in row} for row in rows]
    source = tmp_path/'source.png'
    cv2.imwrite(str(source), image)
    guide = SimpleNamespace(
        annulus=[(0, 0, ((400*scale, 300*scale), (700*scale, 500*scale), 0))],
        summary=lambda: {'method': 'ellipse_estimate' if fitted else 'observed_ellipse_border'},
    )
    monkeypatch.setattr(multi_zone, 'trace_ellipse_border', lambda *a, **k: None if fitted else guide)
    monkeypatch.setattr(detected_layout, 'fitted_guide', lambda *a, **k: guide)
    monkeypatch.setattr(multi_zone, '_inner_rows', lambda *a: rows)
    monkeypatch.setattr(multi_zone, '_arc_maps', lambda *a: {
        key: {'x': np.zeros((30, 100), np.float32), 'y': np.zeros((30, 100), np.float32),
              'degrees': [0, 180], 'radial_range': [.5, .9]}
        for key in ['upper', 'lower']})
    return source, image, rows


@pytest.mark.parametrize('fitted', [False, True])
@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_margin_overlap_accepts_rows_and_only_masks_content(monkeypatch, tmp_path, fitted, scale):
    source, image, rows = layout(monkeypatch, tmp_path, fitted=fitted, scale=scale)
    plan = multi_zone.prepare_multi_zone(source, tmp_path/'out', 0)
    assert plan is not None
    assert len(plan['jobs']) == 5
    mask = cv2.imread(str(plan['mask']), 0)
    core = np.zeros(image.shape[:2], np.uint8)
    for row in rows:
        l, t, r, b = row['box']
        core[t:b, l:r] = 255
    assert not mask[core == 0].any()
    masked = cv2.imread(str(plan['ring_input']))
    np.testing.assert_array_equal(masked[core == 0], image[core == 0])
    assert np.all(masked[mask > 0] == 255)
    # OCR still receives the larger reading crop, not the tighter masking box.
    crop = cv2.imread(str(plan['jobs'][0][1]))
    l, t, r, b = rows[0]['read_box']
    np.testing.assert_array_equal(crop, image[t:b, l:r])


def test_actual_content_overlap_still_rejects(monkeypatch, tmp_path):
    source, _, rows = layout(monkeypatch, tmp_path)
    rows[1]['box'][1] = rows[0]['box'][3]-1
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0) is None
    assert not (tmp_path/'out').exists()


def test_failed_measured_and_fitted_geometry_still_rejects(monkeypatch, tmp_path):
    source, _, _ = layout(monkeypatch, tmp_path, fitted=True)
    monkeypatch.setattr(detected_layout, 'fitted_guide', lambda *a, **k: None)
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0) is None


@pytest.mark.parametrize('scale', [.5, 1, 2])
def test_crossing_row_boxes_with_separate_components_are_accepted(monkeypatch, tmp_path, scale):
    source, image, rows = layout(monkeypatch, tmp_path, scale=scale)
    # Two staggered rows overlap vertically by 5 pixels, but the lower
    # component of the upper row is to the left of the next row's top one.
    rows[1]['box'] = [round(v*scale) for v in [260, 274, 540, 355]]
    rows[1]['read_box'] = [round(v*scale) for v in [252, 266, 548, 363]]
    rows[1]['component_boxes'] = [[round(v*scale) for v in part] for part in
                                [[260, 274, 350, 355], [440, 274, 540, 324]]]
    rows[2]['component_boxes'] = [[round(v*scale) for v in part] for part in
                                [[440, 350, 510, 390], [290, 365, 350, 390]]]
    image[round(274*scale):round(355*scale), round(260*scale):round(540*scale)] = 255
    image[round(350*scale):round(390*scale), round(290*scale):round(510*scale)] = 255
    for row in rows[1:]:
        for l, t, r, b in row['component_boxes']:
            image[t:b, l:r] = (70, 70, 245)
    cv2.imwrite(str(source), image)
    plan = multi_zone.prepare_multi_zone(source, tmp_path/'out', 0)
    assert plan is not None
    assert len(plan['jobs']) == 5
    mask = cv2.imread(str(plan['mask']), 0)
    masked = cv2.imread(str(plan['ring_input']))
    np.testing.assert_array_equal(masked[mask == 0], image[mask == 0])


@pytest.mark.parametrize('missing', [False, True])
def test_shared_or_unproven_components_still_reject(monkeypatch, tmp_path, missing):
    source, _, rows = layout(monkeypatch, tmp_path)
    rows[1]['box'] = [260, 274, 540, 355]
    rows[1]['component_boxes'] = [[300, 274, 400, 355]]
    if not missing:
        rows[2]['component_boxes'] = [[350, 350, 450, 390]]
    assert multi_zone.prepare_multi_zone(source, tmp_path/'out', 0) is None


def test_non_adjacent_crossing_rows_are_also_checked():
    rows = [
        {'box': [200, 200, 500, 400], 'component_boxes': [[200, 200, 300, 400]]},
        {'box': [350, 280, 400, 310], 'component_boxes': [[350, 280, 400, 310]]},
        {'box': [220, 350, 450, 450], 'component_boxes': [[220, 350, 280, 450]]},
    ]
    assert not multi_zone._rows_overlap(rows[0], rows[1])
    assert not multi_zone._rows_overlap(rows[1], rows[2])
    assert multi_zone._rows_overlap(rows[0], rows[2])


def test_inner_row_groups_retain_their_own_component_evidence():
    image = np.full((600, 800, 3), 255, np.uint8)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx-400)/350, (yy-300)/250)
    for y, xs in [(230, [370, 400]), (300, [280, 330, 380, 430, 480]),
                  (370, [310, 360, 410, 460])]:
        for x in xs:
            image[y:y+30, x:x+20] = (70, 70, 245)
    rows = multi_zone._inner_rows(image, rho, (700, 500))
    assert len(rows) == 3
    assert [len(row['component_boxes']) for row in rows] == [2, 5, 4]
    for row in rows:
        l, t, r, b = row['box']
        assert all(l <= x1 < x2 <= r and t <= y1 < y2 <= b
                   for x1, y1, x2, y2 in row['component_boxes'])
