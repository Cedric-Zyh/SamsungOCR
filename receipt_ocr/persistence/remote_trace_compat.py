"""Legacy run ownership inference, isolated from new explicit attempt links.

Only saved evidence is considered. Missing chronology never invents a completed
run or authorizes work; callers supply whether an older remote attempt exists.
"""
from datetime import datetime
import json

from .remote_call_projection import _regular_run, project_regular_only


def retains_regular_run(remote, options, current, *, has_predecessor=False):
    state = json.loads(remote['seal_requests_json'] or '{}')
    if state.get('retained_regular_job_id') == remote['job_id'] and 'retained_regular_attempt' in state:
        # A subsequent local retry may reuse the same job ID with a new attempt.
        return state['retained_regular_attempt'] == remote['attempt']
    if current is not None and current['job_id'] == remote['job_id']:
        saved = json.loads(current['snapshot_json'])
        regular = saved.get('_regular_timing') or {}
        local_start = regular.get('started_at') or saved.get('started_at') or current['started_at']
        try:
            if datetime.fromisoformat(remote['created_at']) < datetime.fromisoformat(local_start):
                return False  # This remote attempt predates the newer local run.
        except (TypeError, ValueError):
            pass
    if state.get('retained_regular_job_id') == remote['job_id']:
        return True
    # Older resubmissions replaced the active remote row but kept the same
    # recognition job and its original local trace. That durable predecessor
    # proves a separate attempt, even when the combined trace was already saved.
    if has_predecessor:
        return True
    # A first independent submission after local completion has no predecessor.
    # Use creation (not upload/query) time: a paused original remote queue must
    # still include its real waiting interval in the shared run.
    if options.get('_independent_dzt'):
        try:
            return datetime.fromisoformat(remote['created_at']) > datetime.fromisoformat(remote['job_finished_at'])
        except (TypeError, ValueError):
            pass
    return False


def newer_local_trace(remote, call, current):
    """Project a later local retry without attaching an already finished remote run."""
    if current is None or current['job_id'] != remote['job_id']:
        return None
    state = json.loads(remote['seal_requests_json'] or '{}')
    retained = state.get('retained_remote_result') or {}
    completed_transfer = retained.get('job_id') == remote['job_id'] and bool(retained.get('finished_at'))
    if remote['status'] not in {'applied', 'conflict', 'failed', 'unknown', 'cancelled'} and not (
            remote['status'] == 'received' and completed_transfer):
        return None
    base = json.loads(current['snapshot_json'])
    regular = _regular_run(base, base.get('_regular_recorded', isinstance(base.get('calls'), list)))
    timing = call.get('transport') or {}
    end = (retained['finished_at'] if completed_transfer else
           timing.get('applied_at') or timing.get('result_received_at') or timing.get('finished_at'))
    try:
        if datetime.fromisoformat(end) >= datetime.fromisoformat(regular['started_at']):
            return None
        transferred = completed_transfer or datetime.fromisoformat(remote['created_at']) < datetime.fromisoformat(remote['job_created_at'])
    except (TypeError, ValueError):
        return None
    # A full run may legitimately start its local branch after the remote one
    # has finished. Only a retry/new job proves that they are separate runs.
    if remote['attempt'] <= 1 and not transferred:
        return None
    return project_regular_only(base)
