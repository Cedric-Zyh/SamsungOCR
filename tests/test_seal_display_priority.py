"""A pending standard check must not hide a better independent OCR reading."""
from copy import deepcopy
import json
import sqlite3

import pytest

from receipt_ocr.domain.seal_standards import apply_standard_check, standard_comparison
from receipt_ocr.web.sample_result_filters import (
    LIST_RESULT_SQL, decode_list_result, project_sample_seal_result, sample_result_filters,
)


REQUIRED = '北京永乐DC010DCM收货专用章②'
PARTIAL = '北京永乐DC010DCM'
WRONG = '北京永乐DC010DCM收费专用章'


def pending_receipt():
    local = {'recognized': PARTIAL, 'status': '未识别', 'reliable': False,
             'requirement': REQUIRED, 'comparison_policy': 'strict_text',
             'backend': '本地 PaddleOCR PP-OCRv6 Small',
             'receiving_review': {'required': True, 'reasons': ['remaining_meaningful_identity_conflict']}}
    remote = {'recognized': WRONG, 'status': '不匹配', 'reliable': True,
              'requirement': REQUIRED, 'source': '清瞳 · 印章文字 OCR', 'backend': '清瞳印章 API',
              'dual_check': {'selected': {
                  'ocr': {'text': WRONG, 'comparison': {'status': '不匹配'}},
                  'template': {'label': REQUIRED[:-1] + '2', 'requirement_match': {'status': '不匹配'}},
              }}}
    return {'fields': {'签章要求': REQUIRED},
            'recognition_config': {'seal': ['paddle_v6', 'qingtong']},
            'seal_check': {**deepcopy(remote), 'status': '需人工复核', 'reliable': False,
                           'comparison_policy': 'manual_standard_text_and_shape',
                           'local_channel': deepcopy(local), 'score': .914,
                           'provider_comparison': {'selected_source': remote['source']}},
            'recognition_variants': {'seal': [
                {'method': 'paddle_v6', 'details': {'seal_check': local}},
                {'method': 'qingtong', 'details': {'seal_check': remote}},
            ]},
            'processing_artifacts': {'seals': [{
                'original_url': '/files/local.png', 'shape_code': 'rectangle', 'color': 'red',
                'matching_texts': [], 'receiving_form': {'managed': True, 'status': 'conflict',
                    'identity_text': '', 'candidate_text': PARTIAL},
            }]},
            'seal_standard_check': {'status': 'unmatched'},
            'date_check': {'status': '未识别'}, 'overall': '需人工复核',
            'review_issues': [{'scope': 'seal', 'message': '需核对身份文字'},
                              {'scope': 'date', 'message': '日期未识别'}]}


def compact(result):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE results(result_json TEXT)')
        db.execute('INSERT INTO results VALUES(?)', (json.dumps(result),))
        value = db.execute('SELECT ' + LIST_RESULT_SQL + ' FROM results r').fetchone()[0]
    return decode_list_result(value)


def test_pending_standard_displays_partial_source_without_adopting_its_identity():
    result = pending_receipt()
    before = deepcopy(result)
    for source in (result, compact(result)):
        selected = sample_result_filters({'status': 'succeeded'}, {}, result=source)['seals']['combined']
        assert (selected['state'], selected['value'], selected['source_key']) == (
            '部分匹配', PARTIAL, 'local:paddle_v6')
    view = project_sample_seal_result(result)
    check = view['seal_check']
    assert (check['status'], check['recognized'], check['display_text']) == ('部分匹配', PARTIAL, PARTIAL)
    assert check['source'] == check['backend'] == '本地 PaddleOCR PP-OCRv6 Small'
    assert check['provider_comparison']['selected_source'] == check['source']
    assert check['reliable'] is False and check['receiving_review']['required'] is True
    assert 'score' not in check  # Do not attach the rejected remote reading's score.
    assert check['comparison_policy'] == 'manual_standard_text_and_shape'
    for key in ('processing_artifacts', 'seal_standard_check', 'date_check', 'review_issues', 'overall'):
        assert view[key] == before[key]
    assert result == before
    assert project_sample_seal_result(view) == view


@pytest.mark.parametrize('extra', [
    {'human_confirmed_match': False},
    {'human_confirmed_match': True},
    {'status': '不匹配'},
])
def test_human_decisions_and_measured_shape_rejection_stay_fixed(extra):
    result = pending_receipt()
    result['seal_check'].update(extra)
    view = project_sample_seal_result(result)
    assert view['seal_check']['recognized'] == WRONG
    assert view['seal_check']['status'] == ('匹配' if extra.get('human_confirmed_match') else '不匹配')


def test_disabled_partial_source_cannot_replace_the_display():
    result = pending_receipt()
    result['recognition_config']['seal'] = ['qingtong']
    assert project_sample_seal_result(result)['seal_check']['recognized'] == WRONG


def test_failed_partial_source_cannot_replace_the_display():
    result = pending_receipt()
    result['recognition_variants']['seal'][0]['error'] = '读取失败'
    assert project_sample_seal_result(result)['seal_check']['recognized'] == WRONG


def test_matching_text_cannot_bypass_pending_standard_shape_check():
    result = pending_receipt()
    result['recognition_variants']['seal'][0]['details']['seal_check'].update(
        recognized=REQUIRED, status='匹配', reliable=True, receiving_review={'required': False})
    for source in (result, compact(result)):
        selected = sample_result_filters({'status': 'succeeded'}, {}, result=source)['seals']['combined']
        assert (selected['state'], selected['value']) == ('部分匹配', REQUIRED)
    check = project_sample_seal_result(result)['seal_check']
    assert check['reliable'] is False


def test_partial_display_keeps_observed_number_over_a_later_bare_purpose_reading():
    result = pending_receipt()
    bare = REQUIRED[:-1]
    local = result['recognition_variants']['seal'][0]['details']['seal_check']
    local.update(recognized=REQUIRED, status='未识别', reliable=False)
    remote = result['recognition_variants']['seal'][1]['details']['seal_check']
    remote.update(recognized=bare, status='部分匹配', reliable=False)
    remote['dual_check']['selected']['ocr'] = {'text': bare, 'comparison': {'status': '部分匹配'}}
    result['seal_check'].update(recognized=bare, local_channel=deepcopy(local), dual_check=deepcopy(remote['dual_check']))
    for source in (result, compact(result)):
        combined = sample_result_filters({'status': 'succeeded'}, {}, result=source)['seals']['combined']
        assert (combined['state'], combined['value'], combined['source_key']) == (
            '部分匹配', REQUIRED, 'local:paddle_v6')
    view = project_sample_seal_result(result)
    assert view['seal_check']['recognized'] == REQUIRED
    assert view['seal_check']['reliable'] is False
    assert view['seal_check']['receiving_review']['required'] is True
    assert view['seal_standard_check'] == result['seal_standard_check']


def test_standard_picker_prioritizes_partial_over_longer_misspelled_stamp():
    result = {'fields': {'签章要求': REQUIRED}, 'processing_artifacts': {'seals': [
        {'original_url': '/files/wrong.png', 'shape_code': 'rectangle', 'reads': [{'text': WRONG}]},
        {'original_url': '/files/partial.png', 'shape_code': 'rectangle', 'reads': [{'text': PARTIAL}]},
    ]}}
    standard = {'id': 1, 'shape': 'rectangle', 'color': 'red'}
    comparison = standard_comparison(result, standard)
    wrong, partial = comparison['candidates']
    assert wrong['score'] > partial['score']
    assert partial['text_status'] == '部分匹配'
    assert comparison['selected_id'] == partial['id']
    assert comparison['status'] == 'unmatched'
    assert apply_standard_check(result, standard)['seal_check']['recognized'] == PARTIAL


def test_conflicting_short_fragment_within_one_stamp_still_cannot_hide_a_wrong_full_reading():
    result = {'fields': {'签章要求': REQUIRED}, 'processing_artifacts': {'seals': [
        {'original_url': '/files/stamp.png', 'reads': [{'text': WRONG}, {'text': PARTIAL}]},
    ]}}
    candidate = standard_comparison(result, None)['candidates'][0]
    assert (candidate['recognized'], candidate['text_status']) == (WRONG, '不匹配')
