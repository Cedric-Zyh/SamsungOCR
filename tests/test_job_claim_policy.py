"""Specialized configuration policies run before the durable claim is published."""
import json

import pytest

from receipt_ocr.jobs.store import JobStore
from test_job_queue import store, queued


def test_claim_policy_snapshot_is_persisted_once_with_the_claim(store):
    job = queued(store)
    class SnapshotStore(JobStore):
        def resolve_claim_options(self, saved_options, current_settings):
            options = super().resolve_claim_options(saved_options, current_settings)
            return {**options, 'configuration_origin': 'custom-snapshot'}

    specialized = SnapshotStore(store.database)
    claimed = specialized.claim()
    assert claimed['id'] == job['id']
    assert json.loads(claimed['payload_json'])['configuration_origin'] == 'custom-snapshot'
    assert store.get(job['id'])['payload_json'] == claimed['payload_json']
    assert claimed['attempt'] == 1 and claimed['status'] == 'running'


def test_failing_claim_policy_does_not_publish_running_state(store):
    job = queued(store)
    class RejectedPolicy(JobStore):
        def resolve_claim_options(self, saved_options, current_settings):
            raise ValueError('invalid specialized configuration')

    with pytest.raises(ValueError, match='specialized configuration'):
        RejectedPolicy(store.database).claim()
    saved = store.get(job['id'])
    assert saved['status'] == 'queued' and saved['attempt'] == 0 and saved['started_at'] == ''
