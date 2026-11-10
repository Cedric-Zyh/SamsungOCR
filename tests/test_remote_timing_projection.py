"""Remote timing summaries expose saved measurements without borrowing old runs."""
from copy import deepcopy

import pytest

from test_job_queue import store
from test_receipt_runs import OPTIONS, save, trace


def qingtong_evidence(*, explicit=False):
    api = {'enabled': True, 'requested': True, 'ok': True,
           'response': {'code': 200, 'processing_time': .68}}
    if explicit:
        api['timing'] = {'request_seconds': 1.206, 'server_seconds': .68}
    return {'seal_check': {'api': api},
            'processing_timings': {'steps': {'seal_api': {'seconds': 1.2144, 'calls': 1}}}}


def service(data, method):
    return next(item for item in data['remote_timings'] if item['method'] == method)


def test_remote_service_totals_are_separate_from_original_recognition_calls(store):
    snapshot = trace()
    qingtong = {**deepcopy(snapshot['calls'][0]), 'method': 'qingtong', 'seconds': 1.206}
    remote = {**deepcopy(snapshot['calls'][0]), 'id': 2, 'method': 'danzhengtong',
              'seconds': 12.277, 'timing_recorded': True, '_remote_task_id': 'accepted',
              'transport': {'elapsed_seconds': 14, 'elapsed_scope': 'submission_to_result',
                            'query_count': 3}}
    snapshot['calls'] = [qingtong, remote]
    snapshot['summary'].update(total=2, succeeded=2)
    identity = save(store.database, snapshot=snapshot, **qingtong_evidence())
    data = store.database.latest_recognition_calls(identity)
    assert data['calls'] == snapshot['calls']
    assert data['summary'] == snapshot['summary']
    assert data['processing_seconds'] == snapshot['processing_seconds']
    qt, dzt = service(data, 'qingtong'), service(data, 'danzhengtong')
    assert qt['request_seconds'] == 1.206 and qt['request_scope'] == 'full'
    assert qt['server_seconds'] == .68
    assert dzt['request_seconds'] == 12.277 and dzt['request_scope'] == 'submit_query'
    assert dzt['elapsed_seconds'] == 14 and dzt['elapsed_approximate']
    assert dzt['query_count'] == 3
    assert dzt['upload_seconds'] is None


@pytest.mark.parametrize('explicit,seconds,scope', [(False, 1.2144, 'legacy_call'), (True, 1.206, 'full')])
def test_old_receipt_can_show_known_remote_timing_without_a_physical_call_trace(store, explicit, seconds, scope):
    identity = save(store.database, **qingtong_evidence(explicit=explicit))
    data = store.database.latest_recognition_calls(identity)
    assert not data['available'] and data['calls'] == [] and data['summary'] is None
    qt = service(data, 'qingtong')
    assert qt['request_seconds'] == seconds and qt['request_scope'] == scope
    assert qt['server_seconds'] == .68
    assert qt['calls'] is None


def test_running_or_failed_retry_cannot_reuse_the_previous_remote_response_times(store):
    db = store.database
    old = trace()
    old['calls'][0].update(method='qingtong', seconds=1.206)
    identity = save(db, snapshot=old, **qingtong_evidence())
    store.create_retries('retry', [identity], {identity: OPTIONS})
    claimed = store.claim()
    assert db.latest_recognition_calls(identity)['remote_timings'] == []
    failed = trace(status='failed')
    failed['calls'][0].update(method='qingtong', seconds=2.3)
    store.finish(claimed, error='provider timeout', recognition_calls=failed)
    qt = service(db.latest_recognition_calls(identity), 'qingtong')
    assert qt['request_seconds'] == 2.3
    assert qt['server_seconds'] is None
    assert qt['status'] == 'failed'
    assert db.get_result(identity)['seal_check']['api']['response']['processing_time'] == .68
