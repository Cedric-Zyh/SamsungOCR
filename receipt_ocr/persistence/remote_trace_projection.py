"""Choose a run's saved evidence, then project it without database or network I/O."""
from dataclasses import dataclass
from datetime import datetime
import json

from ..domain.remote_runs import RemoteRunLink
from .remote_call_projection import combined_remote_call, merge_remote_snapshot, project_regular_only
from .remote_trace_compat import newer_local_trace, retains_regular_run


@dataclass(frozen=True)
class RemoteTraceInput:
    remote: dict
    current: dict | None = None
    has_predecessor: bool = False


def project_remote_trace(evidence: RemoteTraceInput, filename):
    remote, current = evidence.remote, evidence.current
    if current is not None and not current['job_id']:
        try:
            if datetime.fromisoformat(current['started_at']) > datetime.fromisoformat(remote['created_at']):
                return None  # A newer direct recognition replaced this queued run.
        except (TypeError, ValueError):
            pass
    call = combined_remote_call(remote, filename)
    if call is None:
        return None
    state = json.loads(remote['seal_requests_json'] or '{}')
    link = RemoteRunLink.from_json(state.get('run_link'))
    explicit = (link is not None and link.remote_id == remote['id']
                and link.matches(remote['job_id'], remote['attempt'], queued=remote.get('job_status') == 'queued'))
    options = json.loads(remote['payload_json'])
    if explicit:
        if link.remote_evidence == 'retained' and current is not None and current['job_id'] == remote['job_id']:
            return project_regular_only(json.loads(current['snapshot_json']))
        retained = link.local_evidence == 'retained'
    else:
        newer_local = newer_local_trace(remote, call, current)
        if newer_local is not None:
            return newer_local
        retained = retains_regular_run(remote, options, current, has_predecessor=evidence.has_predecessor)
    incremental = bool(options.get('_preserve_provider_results')) or retained
    plan = options.get('recognition_config')
    call['_regular_required'] = (not incremental and options.get('_execution_scope') != 'danzhengtong'
                                 and (not isinstance(plan, dict) or any(
                                     method != 'danzhengtong' for methods in plan.values()
                                     if isinstance(methods, list) for method in methods)))
    base = (json.loads(current['snapshot_json']) if current is not None
            and (incremental or current['job_id'] == remote['job_id']) else {})
    return merge_remote_snapshot(base, call, incremental=incremental)
