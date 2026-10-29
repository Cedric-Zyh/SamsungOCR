"""A known shape conflict cannot become a partial match through OCR display."""
from copy import deepcopy
import json
import sqlite3

import pytest

from receipt_ocr.domain.seal_standards import apply_standard_check
from receipt_ocr.web.sample_result_filters import (
    LIST_RESULT_SQL, decode_list_result, project_sample_seal_result, sample_result_filters,
)


REQUIRED = '郑州广利达电子技术有限公司业务受理专用章'
PARTIAL = '郑州广利达电子技术有限公司业务受理'
STANDARD = {'id': 127, 'shape': 'round', 'color': 'red', 'requirement': REQUIRED}


def stamp(index=0, *, shape='rectangle', text=PARTIAL):
    return {'original_url': f'/files/stamp-{index}.png', 'shape_code': shape,
            'color': 'red', 'reads': [{'text': text, 'confidence': .98}]}


def receipt(*stamps):
    local = {'requirement': REQUIRED, 'recognized': PARTIAL,
             'status': '部分匹配', 'reliable': False, 'message': '印章文字部分匹配'}
    return {'fields': {'签章要求': REQUIRED},
            'recognition_config': {'seal': ['paddle_v6']},
            'processing_artifacts': {'seals': list(stamps)},
            'seal_check': deepcopy(local),
            'recognition_variants': {'seal': [
                {'method': 'paddle_v6', 'details': {'seal_check': local}}]},
            'date_check': {'status': '匹配', 'reliable': True},
            'review_status': '待复核', 'overall': '需人工复核',
            'review_issues': [], 'review_reasons': []}


@pytest.mark.parametrize('text', [REQUIRED, PARTIAL, '', '另一家公司业务受理专用章'])
@pytest.mark.parametrize('actual,expected', [
    ('rectangle', 'round'), ('round', 'rectangle'), ('ellipse', 'round'),
])
def test_known_shape_conflict_rejects_even_with_missing_or_partial_text(text, actual, expected):
    original = receipt(stamp(shape=actual, text=text))
    before = deepcopy(original)
    result = apply_standard_check(original, {**STANDARD, 'shape': expected})
    assert result['seal_check']['status'] == '不匹配'
    assert result['seal_standard_check']['candidates'][0]['shape_status'] == '不匹配'
    assert result['overall'] != '通过'
    assert original == before


def test_sample_projection_keeps_shape_rejection_and_explanation_over_partial_ocr():
    original = receipt(stamp())
    checked = apply_standard_check(original, STANDARD)
    view = project_sample_seal_result(checked)
    assert view['seal_check']['status'] == '不匹配'
    assert view['seal_check']['message'] == '印章形状不符：实际为矩形，标准章为圆形'
    assert view['seal_check']['recognized'] == PARTIAL
    assert view['recognition_variants'] == original['recognition_variants']
    assert view['processing_artifacts'] == original['processing_artifacts']
    assert project_sample_seal_result(view) == view
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE results(result_json TEXT)')
        db.execute('INSERT INTO results VALUES(?)', (json.dumps(checked),))
        compact = decode_list_result(db.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0])
    for result in (checked, compact):
        seals = sample_result_filters({'status': 'succeeded'}, {}, result=result)['seals']
        assert seals['combined']['state'] == '不匹配'
        assert seals['local:paddle_v6']['state'] == '部分匹配'


@pytest.mark.parametrize('shape', ['', 'round'])
@pytest.mark.parametrize('text', ['', PARTIAL])
def test_another_unresolved_stamp_prevents_premature_shape_rejection(shape, text):
    result = apply_standard_check(receipt(stamp(), stamp(1, shape=shape, text=text)), STANDARD)
    assert result['seal_check']['status'] == '需人工复核'
    assert result['overall'] != '通过'


def test_another_fully_matching_stamp_can_still_pass():
    result = apply_standard_check(receipt(stamp(), stamp(1, shape='round', text=REQUIRED)), STANDARD)
    assert result['seal_check']['status'] == '匹配'
    assert result['overall'] == '通过'


def test_correct_shape_with_wrong_company_cannot_rescue_wrong_shape():
    result = apply_standard_check(receipt(stamp(), stamp(1, shape='round', text='另一家公司')), STANDARD)
    assert result['seal_check']['status'] == '不匹配'


@pytest.mark.parametrize('shape', ['', 'round'])
def test_partial_text_without_a_known_shape_conflict_is_not_rejected(shape):
    view = project_sample_seal_result(apply_standard_check(receipt(stamp(shape=shape)), STANDARD))
    assert view['seal_check']['status'] == '部分匹配'


@pytest.mark.parametrize('human', [True, False])
def test_explicit_human_decision_is_preserved(human):
    original = receipt(stamp())
    original['seal_check'].update(human_confirmed_match=human,
                                 status='匹配' if human else '不匹配')
    checked = apply_standard_check(original, STANDARD)
    assert checked['seal_check'] == original['seal_check']
