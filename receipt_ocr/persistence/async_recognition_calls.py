"""Load remote trace evidence and retain the historical projection API."""
from .remote_call_projection import (
    transport_timing, record_transport_phase, remote_call, merge_remote_snapshot,
    combined_remote_call,
)
from .remote_trace_projection import RemoteTraceInput, project_remote_trace


def load_remote_trace(con, filename, current=None):
    """Read one consistent latest-job lane; projection never performs SQL."""
    remote = con.execute('''SELECT d.*,d.rowid AS remote_rowid,j.filename,j.payload_json,j.attempt,
        j.status AS job_status,j.finished_at AS job_finished_at,j.created_at AS job_created_at,
        j.result_id AS job_result_id,j.target_result_id FROM recognition_jobs j
        JOIN danzhengtong_jobs d ON d.job_id=j.id AND d.active=1
        WHERE j.filename=? AND j.rowid=(SELECT MAX(rowid) FROM recognition_jobs WHERE filename=?)''',
        (filename, filename)).fetchone()
    if remote is None:
        return None
    previous = con.execute('''SELECT 1 FROM danzhengtong_jobs
        WHERE job_id=? AND active=0 AND rowid<? LIMIT 1''',
        (remote['job_id'], remote['remote_rowid'])).fetchone()
    return RemoteTraceInput(dict(remote), dict(current) if current is not None else None, bool(previous))


def combined_remote_trace(con, filename, current=None):
    evidence = load_remote_trace(con, filename, current)
    if evidence is None:
        return None
    snapshot = project_remote_trace(evidence, filename)
    return (evidence.remote, snapshot) if snapshot is not None else None
