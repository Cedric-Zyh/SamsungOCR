"""Station layout must protect side glyphs and retain incomplete evidence."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.station_layout import (
    prepare_station_layout, rim_geometry, station_rows,
)
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone


def boxes():
    return [{'text': text, 'confidence': .99,
             'points': [[l, t], [r, t], [r, b], [l, b]]}
            for text, l, t, r, b in [('(2)', 100, 10, 140, 40),
                                    ('取机专用章', 10, 50, 220, 90),
                                    ('6237143站', 30, 100, 210, 130)]]


@pytest.mark.parametrize('case', ['missing', 'unknown', 'weak', 'overlap', 'outside'])
def test_ambiguous_central_rows_reject_station_route(case):
    rows = boxes()
    if case == 'missing':
        rows.pop()
    elif case == 'unknown':
        rows[2]['text'] = '另一行文字'
    elif case == 'weak':
        rows[0]['confidence'] = .5
    elif case == 'overlap':
        rows[1]['points'] = rows[0]['points']
    else:
        rows[0]['points'][0][0] = -1
    assert station_rows(rows, np.array([100, 100]), (150, 240)) == []


def test_polygons_use_full_image_coordinates():
    rows = station_rows(boxes(), np.array([100, 200]), (150, 240))
    assert rows[0]['polygon'][0] == [200, 210]
    assert [r['role'] for r in rows] == ['number', 'type', 'station']


@pytest.mark.parametrize('axis,limit,row_index,point_indexes', [
    (0, 240, 1, [1, 2]), (1, 150, 2, [2, 3]),
])
def test_edge_endpoints_are_clamped_before_global_offset(axis, limit, row_index, point_indexes):
    rows = boxes()
    for index in point_indexes:
        rows[row_index]['points'][index][axis] = limit
    offset = np.array([100, 200])
    result = station_rows(rows, offset, (150, 240))
    assert len(result) == 3
    assert max(p[axis] for p in result[row_index]['polygon']) == limit-1+offset[axis]
    rows[row_index]['points'][point_indexes[0]][axis] = limit + .01
    assert station_rows(rows, offset, (150, 240)) == []


def test_only_observed_interior_ink_is_whitened(tmp_path):
    image = np.full((600, 800, 3), 255, np.uint8)
    cv2.ellipse(image, (400, 300), (350, 250), 0, 0, 360, (40, 40, 255), 6)
    fit, geometry = rim_geometry(image)
    cx, cy, a, b, _ = geometry
    offset = np.array([round(cx-.63*a), round(cy-.60*b)])
    # Mark the measured row pixels and side letters at the same height.
    for row in boxes():
        cv2.fillPoly(image, [np.array(row['points'], np.int32)+offset], (40, 40, 255))
    cv2.rectangle(image, (80, 200), (100, 245), (40, 40, 255), -1)
    cv2.rectangle(image, (700, 200), (720, 245), (40, 40, 255), -1)
    reader = SimpleNamespace(detect_boxes=lambda *args, **kwargs: boxes())
    plan = prepare_station_layout(image, tmp_path, 0, reader=reader, provider='paddle_v6')
    assert plan['method'] == 'ellipse_station_rows'
    mask = cv2.imread(str(plan['mask']), cv2.IMREAD_GRAYSCALE)
    expected = np.zeros(mask.shape, np.uint8)
    for row in plan['rows']:
        cv2.fillPoly(expected, [np.array(row['polygon'], np.int32)], 255)
    expected[~np.any(image < 255, axis=2)] = 0
    np.testing.assert_array_equal(mask, expected)
    assert not mask[200:246, 80:101].any()
    assert not mask[200:246, 700:721].any()
    masked = cv2.imread(str(plan['ring_input']))
    np.testing.assert_array_equal(masked[mask == 0], image[mask == 0])
    assert np.all(masked[mask > 0] == 255)
    assert len(plan['jobs']) == 5


def artifact(phone='电话：02081061101'):
    pairs = [('ring', '三星电子服务中心'), ('layout_inner', '(2)'),
             ('layout_inner', '取机专用章'), ('layout_inner', '6237143站'),
             ('layout_lower', phone)]
    return {'multi_zone_layout': {'method': 'ellipse_station_rows'}, 'reads': [
        {'channel': channel, 'text': text, 'confidence': .99, 'image_url': f'/{i}.png'}
        for i, (channel, text) in enumerate(pairs)]}


def test_station_number_and_contact_survive_composition():
    a = artifact()
    result = finalize_multi_zone(a)
    assert a['combined_text'] == '三星电子服务中心取机专用章(2)6237143站电话:02081061101'
    assert all('(2)' in text for text in result)
    assert a['multi_zone_layout']['status'] == 'recognized'
    assert a['phone_text'] == '02081061101'


@pytest.mark.parametrize('phone', ['电话：021061107', '电话：0061101', '电话：020810611O1'])
def test_high_confidence_short_phone_still_needs_review(phone):
    a = artifact(phone)
    texts = finalize_multi_zone(a)
    assert a['phone_status'] == 'needs_review'
    assert a['multi_zone_layout']['status'] == 'needs_review'
    assert a['lower_arc_text'] == phone
    assert not a.get('phone_text')
    assert all('电话' not in t for t in texts)


def test_missing_or_conflicting_number_does_not_silently_match_without_number():
    for duplicate in [False, True]:
        a = artifact()
        if duplicate:
            a['reads'].append(deepcopy(a['reads'][1]))
        else:
            a['reads'].pop(1)
        assert finalize_multi_zone(a) == []


def test_small_detector_margin_overlap_keeps_tightly_stacked_rows():
    rows = boxes()
    rows[1]['points'] = [[10, 37], [220, 37], [220, 90], [10, 90]]
    assert len(station_rows(rows, np.array([0, 0]), (150, 240))) == 3


def test_reading_from_another_stamp_cannot_satisfy_number_and_phone_together():
    from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
    full_phone_wrong_number = artifact()
    full_phone_wrong_number['reads'][1]['text'] = '(1)'
    right_number_partial_phone = artifact('电话：0061101')
    requirement = '三星电子服务中心取机专用章(2)6237143站电话:02081061101'
    for a in [full_phone_wrong_number, right_number_partial_phone]:
        assert compare_seal_text_strict(requirement, finalize_multi_zone(a))['status'] != '匹配'
