"""A conflicted circle reread cannot be bypassed by an old exact candidate."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.seal_standards import apply_standard_check, seal_candidates

TITLE = '广州花东仓020DCH'
PURPOSE = '收货专用章'
REQUIREMENT = TITLE + PURPOSE + '③'
STANDARD = {'id': 1, 'shape': 'rectangle', 'color': 'red'}


def artifact(index=0, *, managed=False, new_numbers=('②', '②')):
    original = REQUIREMENT
    item = {
        'index': index, 'region_id': f'seal-{index}', 'shape_code': 'rectangle', 'color': 'red',
        'original_url': f'/files/artifacts/test/{index}-original.png',
        'color_isolated_url': f'/files/artifacts/test/{index}-color.png',
        'rectangular_text': original, 'rectangular_row_texts': [TITLE, PURPOSE + '③', ''],
        'matching_texts': [original],
        'reads': [
            {'channel': 'body', 'text': TITLE, 'confidence': .99, 'rectangle_row_index': 0},
            {'channel': 'body', 'text': PURPOSE, 'confidence': .99, 'rectangle_row_index': 1},
            {'channel': 'body', 'text': '③', 'confidence': .99, 'rectangle_row_index': 1},
            *({'channel': 'receiving_number', 'text': token, 'confidence': .99,
               'input_id': f'seal-{index}:number-{scale}', 'rectangle_row_index': 1,
               'matching': False} for scale, token in enumerate(new_numbers, 1)),
        ],
        'receiving_number': {'status': 'conflict', 'row_index': 1,
                             'reason': 'existing_identity_number_or_purpose_conflict'},
    }
    if managed:
        item['receiving_form'] = {'managed': True, 'status': 'adopted',
                                  'selected_source': 'primary', 'identity_text': original,
                                  'primary': {'purpose_row_index': 1, 'identity_text': original}}
    return item


def receipt(*items):
    return {
        'fields': {'签章要求': REQUIREMENT}, 'processing_artifacts': {'seals': list(items)},
        'date_check': {'status': '匹配', 'reliable': True},
        'seal_check': {'status': '匹配', 'reliable': True, 'recognized': REQUIREMENT},
        'review_issues': [], 'review_reasons': [], 'review_status': '待复核',
        'overall': '通过', 'final_result': '通过',
    }


@pytest.mark.parametrize('managed', [False, True])
def test_old_exact_number_is_withheld_for_unmanaged_and_selected_managed_stamp(managed):
    item = artifact(managed=managed)
    before = deepcopy(item)
    result = receipt(item)
    candidate = seal_candidates(result)[0]
    assert candidate['texts'] == []
    assert candidate['text_status'] != '匹配'
    checked = apply_standard_check(result, STANDARD)
    assert checked['seal_standard_check']['status'] == 'unmatched'
    assert checked['seal_check']['reliable'] is False
    assert checked['overall'] != '通过'
    assert item == before
    assert checked['processing_artifacts']['seals'][0] == before


def test_two_high_confidence_different_circle_reads_remain_raw_with_no_active_candidate():
    item = artifact(new_numbers=('②', '③'))
    before = deepcopy(item['reads'])
    candidate = seal_candidates(receipt(item))[0]
    assert candidate['texts'] == []
    assert candidate['text_status'] != '匹配'
    assert [read['text'] for read in item['reads'][-2:]] == ['②', '③']
    assert item['reads'] == before


def test_all_legacy_and_remote_routes_remain_withheld_for_the_conflicted_stamp():
    item = artifact()
    item.update(source='清瞳印章区域', api_index=7, unwrapped_text=TITLE,
                round_type_band_text=PURPOSE + '③', color_isolated_text=REQUIREMENT,
                color_isolated_texts=[REQUIREMENT], combined_text=REQUIREMENT)
    result = receipt(item)
    result['seal_check']['dual_check'] = {'candidates': [{'index': 7, 'ocr': {'text': REQUIREMENT}}]}
    assert seal_candidates(result)[0]['texts'] == []


def test_another_physical_stamp_can_still_satisfy_the_requirement():
    conflicted, valid = artifact(), artifact(1)
    valid.pop('receiving_number')
    checked = apply_standard_check(receipt(conflicted, valid), STANDARD)
    assert checked['seal_standard_check']['status'] == 'matched'
    candidates = checked['seal_standard_check']['candidates']
    assert candidates[0]['texts'] == [] and candidates[0]['matched'] is False
    assert candidates[1]['matched'] is True
    assert checked['seal_check']['status'] == '匹配'
    assert checked['overall'] == '通过'


@pytest.mark.parametrize('confirmed', [True, False])
def test_explicit_human_conclusion_retains_its_existing_authority(confirmed):
    result = receipt(artifact())
    result['seal_check'].update(human_confirmed_match=confirmed,
                                status='匹配' if confirmed else '不匹配', reliable=True)
    before = deepcopy(result['seal_check'])
    checked = apply_standard_check(result, STANDARD)
    assert checked['seal_standard_check']['candidates'][0]['texts'] == []
    assert checked['seal_check'] == before
