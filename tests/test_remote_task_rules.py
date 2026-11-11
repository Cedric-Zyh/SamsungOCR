"""Remote transitions can be replayed without a worker, database, or clock."""
from copy import deepcopy
import json

import pytest

from receipt_ocr.domain.remote_tasks import (
    accept_submission, aggregate_remote, fail_operation, fail_submission,
    finish_query, next_remote_operation, remote_state,
)


def task(*, req_uuid='', marker=None):
    return {'status': 'queued', 'req_uuid': req_uuid,
            'recognition_call_json': json.dumps(marker or {}),
            'seal_requests_json': json.dumps({
                'prepared': True, 'page_required': True, 'seal_required': True,
                'page': {'status': 'queued'}, 'requests': [{'index': 0, 'status': 'queued'}],
            })}


@pytest.mark.parametrize('recover', [False, True])
def test_accepted_uuid_wins_over_stale_submit_and_transport_markers(recover):
    saved = task(req_uuid='accepted', marker={'transport': {'phase': 'submitting'}})
    before = deepcopy(saved)
    state = remote_state(saved, recover=recover)
    assert state['page']['status'] == 'waiting'
    operation = next_remote_operation(saved, state, {'auto_query': True, 'submit_paused': False},
                                      timestamp='2026-01-01T00:00:00+00:00')
    assert operation == ('page_query', None)
    assert saved == before


def test_lost_submit_reply_blocks_that_post_but_not_an_independent_stamp():
    saved = task(marker={'transport': {'phase': 'submitting'}})
    state = remote_state(saved, recover=True)
    assert state['page']['status'] == 'unknown'
    assert aggregate_remote(state)['status'] == 'queued'
    assert next_remote_operation(saved, state, {'auto_query': True, 'submit_paused': False},
                                 timestamp='2026-01-01T00:00:00+00:00') == ('seal_submit', 0)


def test_manual_query_ignores_deadlines_without_authorizing_a_queued_post():
    saved = task(req_uuid='accepted')
    saved['manual_query'] = 1
    state = remote_state(saved)
    state['page']['next_query_at'] = '2099-01-01T00:00:00+00:00'
    assert next_remote_operation(saved, state, {'auto_query': False, 'submit_paused': True},
                                 timestamp='2026-01-01T00:00:00+00:00', prefer_submit=True) == ('page_query', None)


@pytest.mark.parametrize('seconds,expected,error', [(59, 2, ''), (60, 30, ''),
    (599, 30, ''), (600, 120, ''), (1, 30, 'network')])
def test_page_and_stamp_queries_share_the_same_backoff_policy(seconds, expected, error):
    from datetime import datetime, timedelta
    start = datetime.fromisoformat('2026-01-01T00:00:00+00:00')
    observed = (start + timedelta(seconds=seconds)).isoformat()
    update = finish_query(submitted_at=start.isoformat(), observed_at=observed,
                          completed=False, error=error)
    assert datetime.fromisoformat(update['next_query_at']) == start + timedelta(seconds=seconds + expected)
    assert finish_query(submitted_at='', observed_at=observed, completed=True)['next_query_at'] == ''


def test_failed_transport_preserves_uncertain_acceptance_until_uuid_is_known():
    assert fail_submission('lost reply', submitted=True)['status'] == 'unknown'
    assert fail_submission('rejected', submitted=True, rejected=True)['status'] == 'failed'
    accepted = accept_submission(next_query_at='soon')
    accepted.update(fail_operation(accepted, req_uuid='known', error='query timeout', retry_at='later'))
    assert accepted == {'status': 'waiting', 'error': 'query timeout', 'next_query_at': 'later'}
