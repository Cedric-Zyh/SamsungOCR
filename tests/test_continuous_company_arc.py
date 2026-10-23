"""A continuous company arc can extend below the stamp's horizontal centre."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal import pipeline
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone
from receipt_ocr.recognition.seal.preprocess import detected_layout
from receipt_ocr.recognition.seal.preprocess.multi_zone import _arc_maps, _continuous_arc_seam


def arc_image(upper_angles, lower_angles=(), *, scale=1, ratio=.75, color='red'):
    cx, cy, a, b = np.array([500, 400, 440, 440 * ratio]) * scale
    image = np.full((round(800 * scale), round(1000 * scale), 3), 255, np.uint8)
    ink = (40, 40, 255) if color == 'red' else (255, 40, 40)
    cv2.ellipse(image, (round(cx), round(cy)), (round(a), round(b)), 0, 0, 360, ink, 3)
    for angle in [*upper_angles, *lower_angles]:
        t = np.deg2rad([angle - 3, angle + 3, angle + 3, angle - 3])
        radial = np.array([.67, .67, .87, .87])
        polygon = np.column_stack((cx + a * radial * np.cos(t), cy + b * radial * np.sin(t)))
        cv2.fillConvexPoly(image, np.rint(polygon).astype('int32'), ink)
    yy, xx = np.indices(image.shape[:2])
    rho = np.hypot((xx - cx) / a, (yy - cy) / b)
    theta = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    guide = SimpleNamespace(
        annulus=[(1, .001, ((cx, cy), (2 * a, 2 * b), 0))],
        points=np.column_stack((cx + a * np.cos(theta), cy + b * np.sin(theta))),
        summary=lambda: {'method': 'test'},
    )
    return image, rho, guide


@pytest.mark.parametrize('scale', [.5, 1, 2])
@pytest.mark.parametrize('ratio', [.65, 1])
@pytest.mark.parametrize('color', ['red', 'blue'])
def test_low_first_and_last_glyphs_stay_in_one_forward_arc(scale, ratio, color):
    image, rho, guide = arc_image(range(140, 401, 15), scale=scale, ratio=ratio, color=color)
    maps = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(maps) == {'company'}
    mapping = maps['company']
    assert 120 < mapping['degrees'][0] < 150
    assert 380 < mapping['degrees'][1] < 420
    # Every letter stroke belongs to the same sampled arc, including those
    # below the centre on both sides. The outer border stays outside it.
    (cx, cy), _, _ = guide.annulus[0][2]
    ys, xs = np.where((image[:, :, 1] < 100) & (rho < .93))
    angles = np.rad2deg(np.mod(np.arctan2(ys - cy, xs - cx) - np.pi / 2, 2 * np.pi) + np.pi / 2)
    assert angles.min() > mapping['degrees'][0]
    assert angles.max() < mapping['degrees'][1]
    pixels = cv2.remap(image[:, :, 1], mapping['x'], mapping['y'], cv2.INTER_NEAREST)
    assert np.count_nonzero(pixels[:, :pixels.shape[1] // 10] < 100) > 50
    assert np.count_nonzero(pixels[:, -pixels.shape[1] // 10:] < 100) > 50


@pytest.mark.parametrize('color', ['red', 'blue'])
def test_separate_bottom_text_keeps_two_arcs_and_its_own_direction(color):
    image, rho, guide = arc_image(range(185, 356, 15), range(35, 146, 10), color=color)
    maps = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(maps) == {'upper', 'lower'}
    assert maps['upper']['degrees'][0] < maps['upper']['degrees'][1]
    assert maps['lower']['degrees'][0] > maps['lower']['degrees'][1]


@pytest.mark.parametrize('scale', [.5, 1, 2])
@pytest.mark.parametrize('color', ['red', 'blue'])
@pytest.mark.parametrize('joined', ['first', 'last', 'all'])
def test_large_joined_glyphs_do_not_shorten_the_continuous_arc(scale, color, joined):
    image, rho, guide = arc_image(range(140, 401, 15), scale=scale, ratio=1, color=color)
    (cx, cy), (a, b), _ = guide.annulus[0][2]
    start, end = {'first': (140, 200), 'last': (340, 400), 'all': (140, 400)}[joined]
    theta = np.deg2rad(np.arange(start, end + .25, .25))
    points = np.column_stack((cx + a / 2 * .77 * np.cos(theta),
                              cy + b / 2 * .77 * np.sin(theta)))
    ink = (40, 40, 255) if color == 'red' else (255, 40, 40)
    cv2.polylines(image, [np.rint(points).astype('int32')], False, ink, max(2, round(5 * scale)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(
        (((image[:, :, 1] < 100) & (rho < .93))).astype('uint8'), 8)
    assert any(max(w, h) > max(a, b) * .30 for _, _, w, h, _ in stats[1:])
    maps = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(maps) == {'company'}
    mapping = maps['company']
    assert mapping['degrees'][0] < 140
    assert mapping['degrees'][1] > 400
    pixels = cv2.remap(image[:, :, 1], mapping['x'], mapping['y'], cv2.INTER_NEAREST)
    assert np.count_nonzero(pixels[:, :pixels.shape[1] // 10] < 100) > 50
    assert np.count_nonzero(pixels[:, -pixels.shape[1] // 10:] < 100) > 50


def test_joined_components_do_not_change_the_existing_separate_lower_arc():
    image, rho, guide = arc_image(range(185, 356, 15), range(35, 146, 10))
    ordinary = _arc_maps(image, rho, guide)
    checked = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(checked) == set(ordinary) == {'upper', 'lower'}
    for name in ordinary:
        np.testing.assert_array_equal(checked[name]['x'], ordinary[name]['x'])
        np.testing.assert_array_equal(checked[name]['y'], ordinary[name]['y'])


def test_sparse_or_empty_evidence_cannot_declare_a_continuous_name():
    assert _continuous_arc_seam([], 0, 0) is None
    group = [(np.array([0, 1]), np.array([10, 11]))] * 4
    assert _continuous_arc_seam(group, 0, 0) is None


def artifact():
    return {
        'multi_zone_layout': {'method': 'ellipse_continuous_name',
                              'expected_inner_roles': ['type', 'number'], 'arcs': {'company': {}}},
        'reads': [
            {'channel': ch, 'text': text, 'confidence': .99, 'image_url': f'/{i}.png'}
            for i, (ch, text) in enumerate([
                ('layout_inner', '收货专用章'), ('layout_inner', '(1)'),
                ('ring', '深圳欧瑞特供应链管理有限公司杭州分公司'),
            ])
        ],
    }


def test_continuous_name_joins_only_its_observed_type_and_number():
    a = artifact()
    texts = finalize_multi_zone(a)
    expected = '深圳欧瑞特供应链管理有限公司杭州分公司收货专用章(1)'
    assert texts == [expected]
    assert a['reads'][-1]['zone_label'] == '环形公司名称'
    assert a['multi_zone_layout']['status'] == 'recognized'
    assert compare_seal_text_strict(expected, texts)['status'] == '匹配'
    assert compare_seal_text_strict(expected.replace('(1)', '(2)'), texts)['status'] == '不匹配'


@pytest.mark.parametrize('case', ['missing_number', 'weak_name', 'duplicate_number',
                                 'unexpected_phone', 'unexpected_lower', 'wrong_layout'])
def test_incomplete_or_conflicting_readings_do_not_create_complete_evidence(case):
    a = artifact()
    if case == 'missing_number':
        a['reads'].pop(1)
    elif case == 'weak_name':
        a['reads'][-1]['confidence'] = .6
    elif case == 'duplicate_number':
        a['reads'].insert(1, deepcopy(a['reads'][1]))
    elif case in {'unexpected_phone', 'unexpected_lower'}:
        a['reads'].append({'channel': 'layout_lower', 'text': '电话：020-31103112'
                          if case == 'unexpected_phone' else '未确认内容',
                          'confidence': .99, 'image_url': '/lower.png'})
    else:
        a['multi_zone_layout']['arcs']['lower'] = {}
    assert finalize_multi_zone(a) == []
    assert a['multi_zone_layout']['status'] == 'needs_review'


def test_observed_misspelling_is_retained_instead_of_completed():
    a = artifact()
    a['reads'][-1]['text'] = '圳欧瑞特供应链管理有限公司杭州分公'
    texts = finalize_multi_zone(a)
    assert texts == ['圳欧瑞特供应链管理有限公司杭州分公收货专用章(1)']
    assert compare_seal_text_strict(
        '深圳欧瑞特供应链管理有限公司杭州分公司收货专用章(1)', texts,
    )['status'] == '部分匹配'


def test_complete_company_plan_is_dispatched_and_combined_in_pipeline(tmp_path, monkeypatch):
    evidence = artifact()
    inputs, jobs = [], []
    for i, row in enumerate(evidence['reads']):
        item = {'id': f'seal-0:{i}', 'channel': row['channel'], 'image_url': row['image_url'],
                'detected_rows': [TextObservation(row['text'], row['confidence'], 0, 0, 1, 1).to_dict()]}
        inputs.append(item)
        jobs.append((item, tmp_path / f'{i}.png'))
    prepared = {'region_id': 'seal-0', 'reads': [], 'errors': [], 'shape_code': 'round',
                'multi_zone_layout': evidence['multi_zone_layout'], 'inputs': inputs}
    monkeypatch.setattr(pipeline, 'prepare_inputs', lambda *a, **kw: (prepared, jobs))
    expected = '深圳欧瑞特供应链管理有限公司杭州分公司收货专用章(1)'
    texts, artifacts = pipeline._recognize_local_seals(
        tmp_path / 'source.png', [], [SimpleNamespace()], tmp_path / 'out', '', 'paddle_v6',
        requirement=expected,
    )
    assert artifacts[0]['errors'] == []
    assert artifacts[0]['multi_zone_layout']['status'] == 'recognized'
    assert compare_seal_text_strict(expected, texts, mismatch_over_partial=True)['status'] == '匹配'


def test_detected_layout_selects_continuous_geometry_without_using_required_text(tmp_path, monkeypatch):
    image, rho, guide = arc_image(range(140, 401, 15))
    source = tmp_path / 'stamp.png'
    cv2.imwrite(str(source), image)
    boxes = [
        {'text': text, 'confidence': .99, 'points': polygon}
        for text, polygon in [
            ('收货专用章', [[330, 400], [670, 400], [670, 460], [330, 460]]),
            ('(1)', [[460, 470], [540, 470], [540, 510], [460, 510]]),
        ]
    ]
    rows = detected_layout.detected_rows(boxes, image, rho)
    monkeypatch.setattr(detected_layout, '_detect_layout_rows', lambda *a, **kw: (boxes, rows))
    plan = detected_layout.prepare_detected_layout(
        source, tmp_path / 'out', 0, image, guide, rho, reader=object(), provider='paddle_v6',
    )
    assert plan['method'] == 'ellipse_continuous_name'
    assert [job[2] for job in plan['jobs']] == ['layout_inner', 'layout_inner', 'ring']
    assert plan['jobs'][-1][3] == '环形公司名称'
    assert set(plan['arcs']) == {'company'}
