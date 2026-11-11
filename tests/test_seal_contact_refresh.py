"""Only the proven real hotline-only mismatch is refreshed from saved evidence."""
from copy import deepcopy

import pytest

from receipt_ocr.domain.seal_contact_refresh import refresh_seal_contact_results
from receipt_ocr.domain.issues import issue


REQUIRED = '三星手机（南方韵和海雅）服务中心维修专用章'
RAW = '三星手机(南方韵和海雅)服务中心 维修专用章 *维修热线:82533861*'


def saved_result():
    remote = {'requirement': REQUIRED, 'recognized': RAW, 'all_recognized': [RAW],
              'status': '不匹配', 'reliable': True, 'simulated': False,
              'source': '单证通', 'backend': '单证通', 'recognition_mode': 'danzhengtong',
              'comparison_policy': 'strict_text', 'confidence': .91, 'score': .7,
              'message': '印章存在多字', 'number_normalization': None}
    local = {'requirement': REQUIRED, 'recognized': '维修用章', 'status': '部分匹配',
             'reliable': False, 'comparison_policy': 'strict_text', 'source': '本地', 'backend': '本地'}
    qingtong = {'requirement': REQUIRED, 'recognized': '维修专用章', 'status': '部分匹配',
               'reliable': False, 'comparison_policy': 'strict_text',
               'source': '清瞳', 'backend': '清瞳', 'dual_check': {'selected': {}},
               'api': {'ok': True, 'raw': '原始报文'}}
    trace = {'mode': 'real', 'simulated': False, 'status': 'completed',
             'reqUuid': 'saved-request', 'result': {'commitResult': {'收货客户印章': {'value': RAW}}}}
    return {'fields': {'签章要求': REQUIRED, '客户名称': '原客户', '签收日期': '2025-02-21'},
            'recognition_config': {'seal': ['paddle_v6', 'qingtong', 'danzhengtong'], 'acceptance': {
                'seal_match_mode': 'any', 'seal_pass_standard': 'any_exact', 'signature_match_mode': 'none'}},
            'recognition_variants': {'seal': [
                {'method': 'paddle_v6', 'value': ['维修用章', '部分匹配'], 'details': {'seal_check': local}},
                {'method': 'qingtong', 'value': ['维修专用章', '部分匹配'], 'details': {'seal_check': qingtong}},
                {'method': 'danzhengtong', 'value': [RAW, '不匹配'],
                 'details': {'seal_check': remote, 'danzhengtong': trace}},
            ]}, 'seal_check': deepcopy(qingtong), 'date_check': {'actual': '2025-02-21', 'status': '匹配', 'reliable': True},
            'signature_footer': {'present': True, 'method': 'printed_requirement'},
            'signature_check': {'status': '未核对', 'reliable': True}, 'danzhengtong': deepcopy(trace),
            '_recognition_calls': {'calls': [{'method': 'paddle_v6', 'seconds': 5}]},
            'processing_artifacts': {'seals': [{'original_url': '/files/samples/artifacts/seal.png'}]},
            'review_issues': [issue('evidence_unreliable', 'seal', '印章内容无法可靠判断'),
                              issue('provider_conflict', 'seal', '客户印章结果不一致')],
            'review_reasons': ['印章内容无法可靠判断', '客户印章结果不一致'],
            'stage_review_reasons': ['印章内容无法可靠判断'],
            'overall': '需人工复核', 'final_result': '需人工复核', 'review_status': '待复核'}


@pytest.mark.parametrize('raw', [RAW, RAW.replace('*', ''),
    RAW.replace('82533861', '0755-82533861'), RAW.replace(':', '：')])
def test_only_real_dzt_comparison_changes_and_original_observations_remain(raw):
    current = saved_result()
    remote = current['recognition_variants']['seal'][-1]
    remote['details']['seal_check'].update(recognized=raw, all_recognized=[raw])
    remote['value'][0] = raw
    before = deepcopy(current)
    updated = refresh_seal_contact_results(current)
    assert current == before
    assert updated['recognition_variants']['seal'][:2] == before['recognition_variants']['seal'][:2]
    after = updated['recognition_variants']['seal'][-1]
    check = after['details']['seal_check']
    assert check['status'] == '匹配' and check['comparison_policy'] == 'strict_information_fields'
    assert check['field_comparison']['additional_fields'] == ['电话']
    for key in ('recognized', 'all_recognized', 'confidence', 'source', 'backend', 'number_normalization'):
        assert check[key] == before['recognition_variants']['seal'][-1]['details']['seal_check'][key]
    assert after['details']['danzhengtong'] == before['recognition_variants']['seal'][-1]['details']['danzhengtong']
    assert after['value'] == [raw, '匹配']
    assert updated['seal_check']['status'] == '匹配' and updated['seal_check']['reliable'] is True
    assert updated['review_issues'] == updated['review_reasons'] == updated['stage_review_reasons'] == []
    assert updated['overall'] == '通过'
    for key in ('fields', 'date_check', 'signature_footer', 'signature_check', 'danzhengtong',
                '_recognition_calls', 'processing_artifacts'):
        assert updated[key] == before[key]
    assert refresh_seal_contact_results(updated) == updated


@pytest.mark.parametrize('change', ['error', 'pending', 'simulated', 'unreliable', 'mock_trace',
    'waiting_trace', 'missing_real_trace', 'wrong_name', 'short_phone', 'other_addition', 'already_matched',
    'manual', 'manual_field', 'manual_variant', 'confirmed', 'note', 'override', 'stale_requirement'])
def test_non_target_or_untrusted_evidence_is_never_refreshed(change):
    current = saved_result()
    remote = current['recognition_variants']['seal'][-1]
    check, trace = remote['details']['seal_check'], remote['details']['danzhengtong']
    if change in {'error', 'pending'}:
        remote[change] = True
    elif change == 'simulated': check['simulated'] = True
    elif change == 'unreliable': check['reliable'] = False
    elif change == 'mock_trace': trace['mode'] = 'mock'
    elif change == 'waiting_trace': trace['status'] = 'waiting'
    elif change == 'missing_real_trace':
        remote['details'].pop('danzhengtong'); current.pop('danzhengtong')
    elif change == 'wrong_name': check['recognized'] = RAW.replace('海雅', '海芽')
    elif change == 'short_phone': check['recognized'] = RAW.replace('82533861', '8253386')
    elif change == 'other_addition': check['recognized'] = RAW + '地址:深圳市某路1号'
    elif change == 'already_matched': check['status'] = '匹配'
    elif change == 'manual': current['seal_check']['source'] = '人工复核'
    elif change == 'manual_field': current['field_metadata'] = {'客户名称': {'source': '人工复核'}}
    elif change == 'manual_variant': check['human_confirmed_match'] = True
    elif change == 'confirmed': current['review_status'] = '确认通过'
    elif change == 'note': current['human_note'] = '已核对'
    elif change == 'override': current['page_review_override'] = {'fields': {'签章要求': REQUIRED}}
    elif change == 'stale_requirement': current['fields']['签章要求'] = REQUIRED + '（2）'
    assert refresh_seal_contact_results(current) == current


@pytest.mark.parametrize('mode,status', [('any', '匹配'), ('all', '部分匹配'), ('none', '未核对')])
def test_acceptance_modes_are_retained(mode, status):
    current = saved_result()
    current['recognition_config']['acceptance']['seal_match_mode'] = mode
    updated = refresh_seal_contact_results(current)
    assert updated['seal_check']['status'] == status
    if mode != 'any':
        assert updated['review_issues'] == current['review_issues']
        assert updated['overall'] == current['overall']


def test_all_mode_cannot_ignore_another_failed_provider():
    current = saved_result()
    current['recognition_config']['acceptance']['seal_match_mode'] = 'all'
    current['recognition_variants']['seal'][0]['error'] = 'failed'
    updated = refresh_seal_contact_results(current)
    assert updated['seal_check']['status'] == '需人工复核' and not updated['seal_check']['reliable']


def test_required_provider_policy_cannot_be_waived_by_the_new_dzt_match():
    current = saved_result()
    current['recognition_config']['acceptance']['seal_pass_standard'] = 'qingtong_exact'
    updated = refresh_seal_contact_results(current)
    assert updated['seal_check']['source'] == '清瞳' and updated['seal_check']['status'] != '匹配'
    assert updated['review_issues'] == current['review_issues']


@pytest.mark.parametrize('blocking', ['shape_policy', 'standard_unmatched', 'standard_issue'])
def test_standard_shape_blocks_keep_the_aggregate_and_verdict(blocking):
    current = saved_result()
    if blocking == 'shape_policy': current['seal_check']['comparison_policy'] = 'manual_standard_text_and_shape'
    elif blocking == 'standard_unmatched': current['seal_standard_check'] = {'status': 'unmatched'}
    else: current['review_issues'].append(issue('seal_standard', 'seal', '形状不符'))
    updated = refresh_seal_contact_results(current)
    assert updated['seal_check'] == current['seal_check']
    assert updated['review_issues'] == current['review_issues']
    assert updated['overall'] == current['overall']
    assert updated['recognition_variants']['seal'][-1]['details']['seal_check']['status'] == '匹配'


def test_unrelated_date_layout_and_provider_failure_issues_are_retained_verbatim():
    current = saved_result()
    unrelated = [issue('evidence_unreliable', 'date', '日期尚未确认'),
                 issue('provider_conflict', 'fields', '字段有分歧'),
                 issue('footer_unconfirmed', 'document', '页脚尚未确认'),
                 issue('provider_failure', 'seal', '识别服务失败', provider='qingtong')]
    current['review_issues'].extend(deepcopy(unrelated))
    current['review_reasons'].extend(item['message'] for item in unrelated)
    updated = refresh_seal_contact_results(current)
    assert updated['review_issues'] == unrelated
    assert updated['review_reasons'] == [item['message'] for item in unrelated]
    assert updated['overall'] == '需人工复核'


def test_missing_standard_keeps_the_full_receipt_gate_while_fixing_text_comparison():
    current = saved_result()
    current['seal_standard_check'] = {'status': 'missing', 'requirement': REQUIRED}
    updated = refresh_seal_contact_results(current)
    assert updated['seal_check']['status'] == '匹配'
    assert updated['overall'] == '需人工复核' and updated['review_status'] == '待复核'


@pytest.mark.parametrize('trace', [{},
    {'mode': 'real', 'simulated': False, 'status': 'failed'},
    {'mode': 'real', 'simulated': False, 'status': 'completed', 'error': 'query failed'},
    {'mode': 'mock', 'simulated': True, 'status': 'completed'}])
@pytest.mark.parametrize('location', ['variant', 'document'])
def test_independent_seal_failure_never_borrows_completed_page_evidence(trace, location):
    current = saved_result()
    target = current['recognition_variants']['seal'][-1]['details'] if location == 'variant' else current
    target['danzhengtong_seal'] = deepcopy(trace)
    assert refresh_seal_contact_results(current) == current


@pytest.mark.parametrize('container', ['details', 'result'])
def test_completed_independent_seal_evidence_repairs_without_a_page_trace(container):
    current = saved_result()
    current.pop('danzhengtong')
    remote = current['recognition_variants']['seal'][-1]
    details = remote.pop('details')
    details['danzhengtong_seal'] = details.pop('danzhengtong')
    remote[container] = details
    before = deepcopy(current)
    updated = refresh_seal_contact_results(current)
    assert updated['recognition_variants']['seal'][-1][container]['seal_check']['status'] == '匹配'
    assert updated['seal_check']['status'] == '匹配'
    assert updated['recognition_variants']['seal'][-1][container]['danzhengtong_seal'] == details['danzhengtong_seal']
    assert refresh_seal_contact_results(updated) == updated
    assert current == before
