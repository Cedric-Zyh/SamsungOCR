"""Regressions for real OCR script variants and a number remnant below an arc."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.parsing.seal_script import _PAIRS, simplify_seal_text
from receipt_ocr.recognition.seal.preprocess.multi_zone import _arc_maps
from test_continuous_company_arc import arc_image


@pytest.mark.parametrize('expected,actual,status', [
    ('深圳欧瑞特供应链管理有限公司收货专用章(1)',
     '深圳歐瑞特供應鏈管理有限公司收貨專用章(1)', '匹配'),
    ('深圳欧瑞特收货专用章(1)', '歐瑞特收貨專用章(1)', '部分匹配'),
    ('杭州松峰电子科技有限公司', '杭州松雄電子科技有限公司', '不匹配'),
    ('深圳欧瑞特收货专用章(1)', '深圳歐瑞特收貨專用章(2)', '不匹配'),
    ('深圳欧瑞特收货专用章', '深圳歐瑞特收貨專用章(1)', '匹配'),
    ('客户1收货章', '客戶①收貨章', '不匹配'),
    ('太原市伊加壹电子服务总汇', '太原市伊加一電子服務總匯', '不匹配'),
])
def test_script_equivalence_does_not_supply_missing_or_incorrect_content(expected, actual, status):
    result = compare_seal_text_strict(expected, [actual], mismatch_over_partial=True)
    assert result['status'] == status
    assert result['recognized'] == actual
    assert result['all_recognized'] == [actual]
    assert result['reliable'] == (status != '部分匹配')


def test_display_and_comparison_use_the_same_character_table():
    source = (Path(__file__).resolve().parents[1] / 'static/modules/core/seal_script.mjs').read_text(encoding='utf-8')
    literal = source.split('const pairs = Array.from(', 1)[1].split(');', 1)[0]
    assert json.loads(literal) == _PAIRS
    assert simplify_seal_text('深圳歐瑞特收貨專用章（1）') == '深圳欧瑞特收货专用章（1）'
    # Do not apply ambiguous alternatives or compatibility digit folding.
    assert simplify_seal_text('乾徵①壹A/a') == '乾徵①壹A/a'


@pytest.mark.parametrize('scale', [.5, 1, 2])
@pytest.mark.parametrize('color', ['red', 'blue'])
def test_small_inner_bottom_remnant_does_not_split_one_company_name(scale, color):
    image, rho, guide = arc_image(range(140, 401, 15), ratio=1, scale=scale, color=color)
    (cx, cy), (a, b), _ = guide.annulus[0][2]
    ink = (40, 40, 255) if color == 'red' else (255, 40, 40)
    theta = np.deg2rad([99, 107, 107, 99])
    radial = np.array([.54, .54, .64, .64])
    polygon = np.column_stack((cx+a/2*radial*np.cos(theta), cy+b/2*radial*np.sin(theta)))
    cv2.fillConvexPoly(image, np.rint(polygon).astype('int32'), ink)
    maps = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(maps) == {'company'}
    assert maps['company']['degrees'][0] < 140
    assert maps['company']['degrees'][1] > 400


def test_independent_bottom_text_is_not_removed_as_noise():
    image, rho, guide = arc_image(range(185, 356, 15), range(35, 146, 10))
    maps = _arc_maps(image, rho, guide, allow_continuous_name=True)
    assert set(maps) == {'upper', 'lower'}
