"""Saved independent seal evidence must never borrow a full-page stamp read."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.provider_result_refresh import refresh_printed_fields_result, refresh_provider_result
from receipt_ocr.recognition.seal.postprocess.providers import combine_seal_provider_checks


MODEL = 'logistics_samsung_outseal_1503'


def seal_trace(*texts):
    requests = []
    candidates = []
    for index, value in enumerate(texts):
        color, kind = ('蓝色', '椭圆章') if index == 0 else ('红色', '圆章')
        candidates.append({'index': index, 'text': value, 'color': color, 'type': kind})
        requests.append({'mode': 'real', 'simulated': False, 'status': 'completed',
                         'reqUuid': f'seal-{index}', 'index': index,
                         'result': {'commitResult': {
                             '收货客户印章': {'value': value, 'ratio': index + 7},
                             '印章颜色': {'value': color}, '印章类型': {'value': kind}}}})
    return {'mode': 'real', 'simulated': False, 'status': 'completed',
            'model_id': MODEL, 'requests': requests}, candidates


def saved_result(*texts, requirement=None, variants=True):
    texts = texts or ('北京恒远恒信科技发展有限公司 收发货专用章',)
    requirement = requirement or texts[0]
    trace, candidates = seal_trace(*texts)
    page_trace = {'mode': 'real', 'simulated': False, 'status': 'completed',
                  'result': {'commitResult': {
                      '收货客户印章': {'value': '整页旧印章文字'},
                      '签收日期': {'value': '2025-01-04'}}}}
    check = {**compare_seal_text_strict(requirement, [texts[0]]),
             'backend': '单证通', 'source': '单证通', 'simulated': False,
             'recognition_mode': 'danzhengtong', 'model_id': MODEL,
             'provider_color': '蓝色', 'provider_seal_type': '椭圆章',
             'candidates': candidates}
    result = {'fields': {'签章要求': requirement, '要求到货': '2025-01-04'},
              'date_check': {'required': '2025-01-04', 'actual': '2025-01-04',
                             'status': '匹配', 'reliable': True, 'backend': '单证通'},
              'seal_check': deepcopy(check), 'danzhengtong': deepcopy(page_trace),
              'danzhengtong_seal': deepcopy(trace),
              'recognition_config': {'acceptance': {'seal_match_mode': 'any',
                  'date_match_mode': 'any', 'signature_match_mode': 'none'}},
              'recognition_variants': {}, 'review_reasons': [], 'stage_review_reasons': [],
              'processing_artifacts': {'seals': [
                  {'provider': 'danzhengtong', 'index': index, 'provider_text': text,
                   'returned_image_url': f'/files/artifacts/seals/{index}-returned.png',
                   'returned_image_status': 'downloaded'} for index, text in enumerate(texts)]}}
    if variants:
        result['recognition_variants']['seal'] = [{'method': 'danzhengtong', 'details': {
            'seal_check': deepcopy(check), 'danzhengtong': deepcopy(page_trace),
            'danzhengtong_seal': deepcopy(trace)}}]
    return result


@pytest.mark.parametrize('variants', [True, False])
def test_independent_scene_wins_over_full_page_and_preserves_saved_images(variants, monkeypatch):
    from receipt_ocr import danzhengtong_seal

    def forbidden(*args, **kwargs):
        raise AssertionError('saved evidence refresh must not perform network or image work')

    monkeypatch.setattr(danzhengtong_seal, 'download_returned_image', forbidden)
    monkeypatch.setattr(danzhengtong_seal.Client, 'recognize', forbidden)
    current = saved_result(variants=variants)
    before = deepcopy(current)
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == current['fields']['签章要求']
    assert updated['seal_check']['status'] == '匹配'
    assert updated['seal_check']['model_id'] == MODEL
    assert updated['seal_check']['provider_color'] == '蓝色'
    assert updated['seal_check']['provider_seal_type'] == '椭圆章'
    assert updated['seal_check']['provider_metadata']['收货客户印章']['provider_ratio'] == 7
    assert updated['processing_artifacts'] == before['processing_artifacts']
    assert updated['danzhengtong'] == before['danzhengtong']
    assert updated['danzhengtong_seal'] == before['danzhengtong_seal']
    assert current == before
    assert refresh_provider_result(updated) == updated


def test_date_still_uses_the_page_trace_while_seal_uses_its_independent_trace():
    current = saved_result()
    current['date_check']['actual'] = '2025-01-03'
    current['date_check']['status'] = '不匹配'
    updated = refresh_provider_result(current)
    assert updated['date_check']['actual'] == '2025-01-04'
    assert updated['date_check']['status'] == '匹配'
    assert updated['seal_check']['recognized'] != '整页旧印章文字'


def test_variant_independent_trace_takes_precedence_over_global_independent_trace():
    current = saved_result()
    current['danzhengtong_seal'], _ = seal_trace('其他运行的全局结果')
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == current['fields']['签章要求']
    assert updated['seal_check']['status'] == '匹配'


@pytest.mark.parametrize('trace', [{}, {'mode': 'real', 'simulated': False, 'status': 'failed'},
                                  {'mode': 'mock', 'simulated': True, 'status': 'completed'}])
def test_empty_failed_or_simulated_independent_trace_never_falls_back_to_page(trace):
    current = saved_result()
    current['danzhengtong_seal'] = deepcopy(trace)
    current['recognition_variants']['seal'][0]['details']['danzhengtong_seal'] = deepcopy(trace)
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == current['seal_check']['recognized']
    assert updated['seal_check']['provider_color'] == '蓝色'


def test_two_physical_stamp_fragments_cannot_be_joined_into_a_complete_match():
    current = saved_result('甲乙', '丙丁', requirement='甲乙丙丁')
    # The page trace would provide a false exact match if used as fallback.
    current['danzhengtong']['result']['commitResult']['收货客户印章']['value'] = '甲乙丙丁'
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] in {'甲乙', '丙丁'}
    assert updated['seal_check']['status'] == '部分匹配'
    assert updated['seal_check']['reliable'] is False
    assert updated['seal_check']['all_recognized'] == ['甲乙', '丙丁']
    assert [item['text'] for item in updated['seal_check']['candidates']] == ['甲乙', '丙丁']


def test_exact_shipper_stamp_cannot_replace_a_wrong_customer_stamp_when_refreshing():
    expected = '客户甲收货专用章'
    current = saved_result(expected, '客户乙收货专用章', requirement=expected)
    for trace in (current['danzhengtong_seal'],
                  current['recognition_variants']['seal'][0]['details']['danzhengtong_seal']):
        trace['requests'][0]['region'] = {'role': '发货单位章'}
        trace['requests'][1]['region'] = {'role': '收货客户章'}
    for check in (current['seal_check'],
                  current['recognition_variants']['seal'][0]['details']['seal_check']):
        check['candidates'] = [check['candidates'][1]]
    before = deepcopy(current)
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == '客户乙收货专用章'
    assert updated['seal_check']['status'] == '不匹配'
    assert updated['seal_check']['provider_color'] == '红色'
    assert updated['seal_check']['candidates'] == [
        {'index': 1, 'text': '客户乙收货专用章', 'color': '红色', 'type': '圆章'}]
    assert updated['processing_artifacts'] == before['processing_artifacts']
    assert updated['danzhengtong_seal'] == before['danzhengtong_seal']


def test_excluded_shipper_only_trace_cannot_be_reintroduced_from_old_saved_candidates():
    expected = '客户甲收货专用章'
    current = saved_result(expected)
    for trace in (current['danzhengtong_seal'],
                  current['recognition_variants']['seal'][0]['details']['danzhengtong_seal']):
        trace['requests'][0]['region'] = {'role': '发货单位章'}
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == ''
    assert updated['seal_check']['status'] == '未识别'
    assert updated['seal_check']['candidates'] == []


@pytest.mark.parametrize('marker', [{'pending': True}, {'error': 'failed'}, {'mode': 'mock'},
                                    {'simulated': True}])
def test_inactive_child_request_is_not_evidence_even_if_parent_and_child_status_completed(marker):
    current = saved_result('客户甲收货专用章')
    for trace in (current['danzhengtong_seal'],
                  current['recognition_variants']['seal'][0]['details']['danzhengtong_seal']):
        trace['requests'][0].update(marker)
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == ''
    assert updated['seal_check']['candidates'] == []
    assert updated['seal_check']['reliable'] is False


@pytest.mark.parametrize('refresh', [refresh_printed_fields_result, refresh_provider_result])
def test_reselected_requirement_chooses_another_physical_stamp_and_its_own_attributes(refresh):
    old, new = '客户甲收货专用章', '客户乙收发货专用章'
    current = saved_result(old, new)
    current['recognition_variants']['fields'] = [{'method': 'paddle_v6', 'details': {
        'fields': {'签章要求': new}, 'field_metadata': {
            '签章要求': {'value': new, 'original': new, 'source': 'paddle_v6',
                         'confidence': .99, 'low_confidence': False}}}}]
    before = deepcopy(current)
    updated = refresh(current)
    assert updated['fields']['签章要求'] == new
    assert updated['seal_check']['requirement'] == new
    assert updated['seal_check']['recognized'] == new
    assert updated['seal_check']['status'] == '匹配'
    assert updated['seal_check']['provider_color'] == '红色'
    assert updated['seal_check']['provider_seal_type'] == '圆章'
    assert updated['seal_check']['provider_metadata']['收货客户印章']['provider_ratio'] == 8
    assert updated['processing_artifacts'] == before['processing_artifacts']
    assert updated['danzhengtong_seal'] == before['danzhengtong_seal']
    assert current == before
    assert refresh(updated) == updated


def test_dedicated_saves_without_child_traces_use_saved_stamp_candidates_without_page_fallback():
    current = saved_result('甲乙', '丙丁', requirement='甲乙丙丁')
    for trace in (current['danzhengtong_seal'],
                  current['recognition_variants']['seal'][0]['details']['danzhengtong_seal']):
        trace.pop('requests')
        trace['result'] = {'commitResult': {'收货客户印章': {'value': '甲乙丙丁'}}}
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] in {'甲乙', '丙丁'}
    assert updated['seal_check']['status'] == '部分匹配'


def test_legacy_page_only_history_keeps_its_existing_refresh_contract():
    current = saved_result()
    current.pop('danzhengtong_seal')
    current['recognition_variants']['seal'][0]['details'].pop('danzhengtong_seal')
    for check in (current['seal_check'], current['recognition_variants']['seal'][0]['details']['seal_check']):
        check.pop('model_id')
        check.pop('candidates')
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == '整页旧印章文字'


def test_an_incomplete_dedicated_save_without_its_trace_cannot_borrow_page_evidence():
    current = saved_result()
    current.pop('danzhengtong_seal')
    current['recognition_variants']['seal'][0]['details'].pop('danzhengtong_seal')
    updated = refresh_provider_result(current)
    assert updated['seal_check']['recognized'] == current['fields']['签章要求']
    assert updated['seal_check']['provider_color'] == '蓝色'


@pytest.mark.parametrize('human', [{'review_status': '确认通过'}, {'human_note': '已人工核对'},
                                  {'seal_check': {'human_confirmed_match': False}}])
def test_human_decisions_still_preserve_the_entire_record(human):
    current = saved_result()
    current.update(human)
    assert refresh_provider_result(current) == current


def test_aggregate_copies_remote_attributes_when_a_qingtong_base_loses_to_dedicated_scene():
    current = saved_result()
    remote = refresh_provider_result(current)['seal_check']
    qingtong = {'dual_check': {'selected': {'index': 0}}, 'backend': '清瞳',
                'requirement': remote['requirement'], 'recognized': '其他公司',
                'status': '不匹配', 'reliable': True}
    combined = combine_seal_provider_checks([qingtong, remote])
    assert combined['recognized'] == remote['recognized']
    assert combined['model_id'] == MODEL
    assert combined['provider_color'] == '蓝色'
    assert combined['provider_seal_type'] == '椭圆章'
    assert combined['provider_metadata'] == remote['provider_metadata']
    assert combined['candidates'] == remote['candidates']
    assert combined['qingtong_channel'] == qingtong
