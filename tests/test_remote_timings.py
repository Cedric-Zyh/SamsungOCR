"""Remote summaries keep HTTP, elapsed lifecycle and old timing scopes distinct."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.persistence.remote_timings import remote_interface_timings


def row(method, seconds=None, **extra):
    return {'method': method, 'seconds': seconds, 'status': 'succeeded', **extra}


def remote(rows=None, transports=None, evidence=None):
    return {item['method']: item for item in remote_interface_timings(
        {'calls': rows or [], 'transport_calls': transports or []}, evidence)}


def qingtong_evidence(api, seconds=None):
    check = {'api': api}
    return {'recognition_variants': {'seal': [
        {'method': 'qingtong', 'details': {'seal_check': check}},
    ]}, 'seal_check': {'qingtong_channel': deepcopy(check), 'api': deepcopy(api)},
        'processing_timings': {'steps': {'seal_api': {'calls': 1, 'seconds': seconds}}}}


def test_saved_42_call_run_separates_qingtong_http_and_legacy_async_submit_query_time():
    snapshot = {'summary': {'total': 42}, 'processing_seconds': 42.5,
        'calls': [row('paddle_v6', .5) for _ in range(40)] + [
            row('qingtong', 1.206),
            row('danzhengtong', 12.277, _remote_task_id=206, timing_recorded=True,
                transport={'request_seconds': None, 'measured_request_seconds': 12.277,
                    'upload_seconds': None, 'submit_seconds': None, 'query_seconds': None,
                    'query_count': 1, 'elapsed_seconds': 14, 'elapsed_scope': 'submission_to_result',
                    'complete': False}),
        ]}
    evidence = qingtong_evidence({'ok': True, 'response': {'processing_time': .68}}, seconds=1.2144)
    before = deepcopy(snapshot)
    timings = {item['method']: item for item in remote_interface_timings(snapshot, evidence)}
    qt, dzt = timings['qingtong'], timings['danzhengtong']
    assert qt['calls'] == 1 and qt['request_seconds'] == 1.206 and qt['request_scope'] == 'full'
    assert qt['server_seconds'] == .68
    assert qt['elapsed_seconds'] == 1.206 and qt['elapsed_scope'] == 'request'
    assert dzt['calls'] == 1 and dzt['request_seconds'] == 12.277
    assert dzt['request_scope'] == 'submit_query'
    assert dzt['upload_seconds'] is None
    assert dzt['elapsed_seconds'] == 14 and dzt['elapsed_scope'] == 'submission_to_result'
    assert dzt['elapsed_approximate'] is True
    assert '未记录上传' in dzt['note'] and '包含等待' in dzt['note']
    assert snapshot == before  # Physical call totals and existing clocks stay untouched.


def test_complete_transport_measures_upload_and_requests_without_changing_legacy_call_seconds():
    call = row('danzhengtong', 8, _remote_task_id=1, timing_recorded=True,
        transport={'request_seconds': 10, 'measured_request_seconds': 10,
            'upload_seconds': 2, 'submit_seconds': 3, 'query_seconds': 5, 'query_count': 2,
            'elapsed_seconds': 35, 'elapsed_scope': 'upload_to_result', 'complete': True})
    result = remote([call])['danzhengtong']
    assert result['request_seconds'] == 10 and result['request_scope'] == 'full'
    assert [result[key] for key in ('upload_seconds', 'submit_seconds', 'query_seconds', 'query_count')] == [2, 3, 5, 2]
    assert result['elapsed_seconds'] == 35 and result['elapsed_scope'] == 'upload_to_result'
    assert result['elapsed_approximate'] is False
    assert call['seconds'] == 8


def test_page_and_stamp_lifecycles_use_shared_span_while_http_phases_are_summed():
    calls = [row('danzhengtong', 3, _remote_task_id=identity, timing_recorded=True,
                 transport={'request_seconds': 4, 'upload_seconds': 1, 'submit_seconds': 1,
                            'query_seconds': 2, 'query_count': 1, 'elapsed_seconds': end - start,
                            'elapsed_scope': 'upload_to_result', 'complete': True,
                            'started_at': f'2026-01-01T00:00:{start:02d}+00:00',
                            'result_received_at': f'2026-01-01T00:00:{end:02d}+00:00'})
             for identity, start, end in [('page', 0, 20), ('stamp-0', 2, 23), ('stamp-1', 4, 25)]]
    result = remote(calls)['danzhengtong']
    assert result['calls'] == 3 and result['request_seconds'] == 12
    assert result['elapsed_seconds'] == 25
    assert result['upload_seconds'] == 3 and result['query_seconds'] == 6


def test_partial_transport_never_claims_a_full_http_total():
    call = row('danzhengtong', 4.5, _remote_task_id=2, timing_recorded=True,
        transport={'request_seconds': None, 'measured_request_seconds': 6.5,
            'upload_seconds': 2, 'submit_seconds': None, 'query_seconds': 4.5, 'query_count': 1,
            'elapsed_seconds': 22, 'elapsed_scope': 'upload_to_result', 'complete': False})
    result = remote([call])['danzhengtong']
    assert result['request_seconds'] == 6.5 and result['request_scope'] == 'partial'
    assert result['submit_seconds'] is None
    assert '部分请求计时缺失' in result['note']


def test_old_unmeasured_accepted_request_keeps_duration_unknown_but_known_elapsed():
    result = remote([row('danzhengtong', None, _remote_task_id=3, timing_recorded=False,
        transport={'request_seconds': None, 'measured_request_seconds': None,
            'elapsed_seconds': 14, 'elapsed_scope': 'submission_to_result'})])['danzhengtong']
    assert result['calls'] == 1
    assert result['request_seconds'] is None and result['request_scope'] == 'unknown'
    assert result['elapsed_seconds'] == 14
    assert result['upload_seconds'] is result['submit_seconds'] is result['query_seconds'] is None


def test_old_synchronous_call_is_not_mislabeled_as_http_request_time():
    result = remote([row('danzhengtong', 8)])['danzhengtong']
    assert result['request_seconds'] == 8 and result['request_scope'] == 'legacy_call'
    assert result['elapsed_seconds'] is None and result['elapsed_scope'] is None
    assert '可能含等待' in result['note']


def test_duplicate_remote_task_in_physical_and_transport_projections_counts_once():
    physical = row('danzhengtong', 4, _remote_task_id=5, timing_recorded=True)
    transport = row('danzhengtong', 4, _remote_task_id=5, recognition_invoked=True,
        transport={'request_seconds': 6, 'upload_seconds': 2, 'submit_seconds': 1,
                   'query_seconds': 3, 'query_count': 1})
    result = remote([physical, physical], [transport])['danzhengtong']
    assert result['calls'] == 1 and result['request_seconds'] == 6
    assert result['upload_seconds'] == 2 and result['query_count'] == 1


@pytest.mark.parametrize('flag', ['invoked', 'recognition_invoked'])
def test_upload_failure_is_visible_without_counting_an_unsubmitted_recognition(flag):
    transport = row('danzhengtong', None, _remote_task_id=6, status='failed', **{flag: False},
        transport={'request_seconds': 2, 'upload_seconds': 2, 'submit_seconds': 0,
                   'query_seconds': 0, 'query_count': 0, 'complete': True})
    result = remote(transports=[transport])['danzhengtong']
    assert result['status'] == 'failed' and result['calls'] == 0
    assert result['request_seconds'] == 2 and result['request_scope'] == 'full'
    assert '不计入识别调用次数' in result['note']


def test_upload_projection_and_later_invocation_of_same_task_are_one_logical_call():
    upload = row('danzhengtong', None, _remote_task_id=7, invoked=False, status='failed')
    submitted = row('danzhengtong', 1, _remote_task_id=7, timing_recorded=True,
        transport={'request_seconds': 2})
    result = remote([submitted], [upload])['danzhengtong']
    assert result['calls'] == 1 and result['request_seconds'] == 2
    assert result['status'] == 'succeeded'


def test_interrupted_submission_does_not_claim_the_request_was_never_submitted():
    call = row('danzhengtong', None, _remote_task_id=8, recognition_invoked=False,
               submission_unknown=True, status='unknown')
    result = remote(transports=[call])['danzhengtong']
    assert result['calls'] == 0
    assert '提交状态待确认；无法确认的识别未计入次数' in result['note']
    assert '尚未提交识别' not in result['note']


@pytest.mark.parametrize('seconds,scope,total', [
    ([1, 2], 'full', 3), ([1, None], 'partial', 1), ([None, None], 'unknown', None),
])
def test_multiple_qingtong_requests_do_not_turn_missing_measurements_into_zero(seconds, scope, total):
    result = remote([row('qingtong', value) for value in seconds])['qingtong']
    assert result['calls'] == 2 and result['request_seconds'] == total
    assert result['request_scope'] == scope
    assert result['elapsed_seconds'] == (total if scope == 'full' else None)


def test_mixed_full_and_submit_only_requests_are_explicitly_partial():
    result = remote([
        row('danzhengtong', 2, _remote_task_id=1, timing_recorded=True),
        row('danzhengtong', 3, _remote_task_id=2, transport={'request_seconds': 4}),
    ])['danzhengtong']
    assert result['calls'] == 2 and result['request_seconds'] == 6
    assert result['request_scope'] == 'partial'
    assert result['upload_seconds'] is None


def test_legacy_wall_time_is_not_summed_with_new_http_measurements():
    result = remote([row('danzhengtong', 12), row('danzhengtong', 2, transport={'request_seconds': 3})])['danzhengtong']
    assert result['request_seconds'] is None and result['request_scope'] == 'unknown'


@pytest.mark.parametrize('status,expected', [('failed', 'failed'), ('running', 'running')])
def test_failed_and_active_request_statuses_remain_visible(status, expected):
    result = remote([row('qingtong', .25, status='succeeded'), row('qingtong', None, status=status)])['qingtong']
    assert result['status'] == expected
    assert result['request_seconds'] == .25 and result['request_scope'] == 'partial'


def test_explicit_qingtong_fallback_reports_http_without_inventing_call_count():
    api = {'enabled': True, 'requested': True, 'ok': True,
        'timing': {'request_seconds': 2.5, 'server_seconds': .5},
        'response': {'processing_time': .5}}
    evidence = qingtong_evidence(api, seconds=2.8)
    before = deepcopy(evidence)
    result = remote(evidence=evidence)['qingtong']
    assert result['calls'] is None and result['request_seconds'] == 2.5 and result['request_scope'] == 'full'
    assert result['server_seconds'] == .5
    assert len(remote_interface_timings({}, evidence)) == 1  # Copies are not extra API requests.
    assert evidence == before


def test_qingtong_legacy_method_fallback_is_labeled_broader_than_http():
    result = remote(evidence=qingtong_evidence({'ok': True, 'response': {'processing_time': .37}}, seconds=1.0875))['qingtong']
    assert result['calls'] is None
    assert result['request_seconds'] == 1.0875 and result['request_scope'] == 'legacy_call'
    assert result['elapsed_seconds'] is None
    assert result['server_seconds'] == .37
    assert '含响应处理' in result['note'] and '并非精确HTTP' in result['note']


def test_server_only_history_never_fabricates_a_network_duration():
    result = remote(evidence=qingtong_evidence({'ok': True, 'response': {'processing_time': .37}}))['qingtong']
    assert result['server_seconds'] == .37
    assert result['request_seconds'] is None and result['request_scope'] == 'unknown'


@pytest.mark.parametrize('api', [
    {'enabled': False, 'requested': True, 'response': {'processing_time': .5}},
    {'enabled': True, 'requested': False, 'timing': {'request_seconds': 0}},
    {'enabled': False}, {},
])
def test_disabled_or_unattempted_api_does_not_create_an_interface_row(api):
    assert remote_interface_timings({}, qingtong_evidence(api, seconds=0)) == []


@pytest.mark.parametrize('value', [True, False, -1, float('inf'), float('nan'), 10**1000, '0.5', {}, []])
def test_invalid_server_values_are_unknown_without_changing_valid_http_time(value):
    result = remote([row('qingtong', 1)], evidence=qingtong_evidence({
        'ok': True, 'timing': {'server_seconds': value}, 'response': {'processing_time': value},
    }))['qingtong']
    assert result['request_seconds'] == 1 and result['server_seconds'] is None


def test_real_zero_measurement_is_distinct_from_unknown():
    result = remote([row('qingtong', 0)], evidence=qingtong_evidence({'ok': True, 'response': {'processing_time': 0}}))['qingtong']
    assert result['request_seconds'] == result['server_seconds'] == 0
    assert result['request_scope'] == 'full'


def test_summary_does_not_copy_credentials_urls_messages_or_image_fields():
    secret = 'API_KEY_SECRET https://private.invalid /private/secret.png'
    result = remote_interface_timings({'calls': [row('qingtong', 1, error=secret, input_name=secret, image_url=secret)]},
        qingtong_evidence({'ok': True, 'response': {'processing_time': .2, 'secret': secret}}))
    assert secret not in json.dumps(result)
    assert set(result[0]) == {
        'method', 'label', 'status', 'calls', 'request_seconds', 'request_scope',
        'elapsed_seconds', 'elapsed_scope', 'elapsed_approximate', 'upload_seconds',
        'submit_seconds', 'query_seconds', 'query_count', 'server_seconds', 'note',
    }
