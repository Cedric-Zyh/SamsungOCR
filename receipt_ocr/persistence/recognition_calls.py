"""The latest physical recognition calls, stored once per complete filename."""

from contextlib import nullcontext
from datetime import datetime
import json
from math import isfinite


def trace_timestamp():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def initialize_recognition_calls(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS latest_recognition_calls (
        filename TEXT PRIMARY KEY,
        run_key TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        status TEXT NOT NULL,
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        result_id INTEGER,
        job_id TEXT NOT NULL DEFAULT '',
        attempt INTEGER NOT NULL DEFAULT 0
    )""")
    # A removed receipt must not leave traces or artifact links behind. Deleting
    # an unrelated older result does not discard the newer receipt's trace.
    for event, condition in (("DELETE", "1"), ("UPDATE OF deleted_at", "NEW.deleted_at<>''")):
        name = "delete" if event == "DELETE" else "soft_delete"
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS recognition_calls_{name}
            AFTER {event} ON results WHEN {condition}
            BEGIN
                DELETE FROM latest_recognition_calls
                WHERE filename=OLD.filename AND (
                    result_id=OLD.id OR NOT EXISTS (
                        SELECT 1 FROM results WHERE filename=OLD.filename AND deleted_at=''
                    )
                );
            END""")


def take_recognition_calls(result):
    """Remove transient tracing data before any result/review JSON is saved."""
    if not isinstance(result, dict):
        return None, ""
    return result.pop("_recognition_calls", None), result.pop("_recognition_call_key", "")


def _time(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0


def _seconds(value):
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        number = float(value)
        return number if isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _legacy_initialization(con, row, snapshot):
    """Recover measured setup only from the result belonging to this trace."""
    if row['status'] not in {'completed', 'succeeded'} or not row['result_id']:
        return None
    recorded = con.execute("""SELECT updated_at,
        json_extract(result_json,'$.queue.job_id') AS job_id,
        json_extract(result_json,'$.processing_timings.steps') AS steps_json
        FROM results WHERE id=? AND deleted_at=''""", (row['result_id'],)).fetchone()
    if recorded is None or not recorded['steps_json']:
        return None
    if row['job_id']:
        same_run = recorded['job_id'] == row['job_id']
    else:
        start, end, updated = map(_time, (snapshot.get('started_at'), snapshot.get('finished_at'), recorded['updated_at']))
        same_run = start > 0 and end >= start and start - 1 <= updated <= end + 1
    if not same_run:
        return None
    steps = json.loads(recorded['steps_json'])
    if not isinstance(steps, dict):
        return None
    values = [_seconds(value.get('seconds')) for name, value in steps.items()
              if (name.endswith('_model_setup') or name == 'paddle_text_box_setup') and isinstance(value, dict)]
    return sum(values) if values and all(value is not None for value in values) else None


class RecognitionCallOperationsMixin:
    def sync_remote_recognition_calls(self, con, job_id):
        from .async_recognition_calls import combined_remote_trace
        job = con.execute('SELECT filename FROM recognition_jobs WHERE id=?', (job_id,)).fetchone()
        if job is None:
            return
        current = con.execute('SELECT * FROM latest_recognition_calls WHERE filename=?', (job['filename'],)).fetchone()
        combined = combined_remote_trace(con, job['filename'], current)
        if combined is None or combined[0]['job_id'] != job_id:
            return
        remote, snapshot = combined
        result_id = remote['result_id'] or remote['job_result_id'] or remote['target_result_id']
        if result_id and con.execute("SELECT 1 FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone() is None:
            return
        con.execute('''INSERT INTO latest_recognition_calls
            (filename,run_key,started_at,status,snapshot_json,result_id,job_id,attempt)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(filename) DO UPDATE SET
            run_key=excluded.run_key,started_at=excluded.started_at,status=excluded.status,
            snapshot_json=excluded.snapshot_json,result_id=excluded.result_id,
            job_id=excluded.job_id,attempt=excluded.attempt''',
            (job['filename'], f"{job_id}:{remote['attempt']}", snapshot['started_at'] or trace_timestamp(),
             'succeeded' if snapshot['status'] == 'completed' else snapshot['status'],
             json.dumps(snapshot, ensure_ascii=False), result_id, job_id, remote['attempt']))

    def begin_recognition_calls(self, filename, run_key, *, job_id="", attempt=0,
                                result_id=None, _connection=None):
        with (nullcontext(_connection) if _connection is not None else self.connect()) as con:
            con.execute("""INSERT INTO latest_recognition_calls
                (filename,run_key,started_at,status,snapshot_json,result_id,job_id,attempt)
                VALUES(?,?,?,'running','{}',?,?,?)
                ON CONFLICT(filename) DO UPDATE SET run_key=excluded.run_key,
                    started_at=excluded.started_at,status='running',snapshot_json='{}',
                    result_id=excluded.result_id,job_id=excluded.job_id,attempt=excluded.attempt""",
                (filename, run_key, trace_timestamp(), result_id, job_id, attempt))

    def save_recognition_calls(self, filename, snapshot, *, result_id=None, run_key="",
                               status="succeeded", _connection=None):
        with (nullcontext(_connection) if _connection is not None else self.connect()) as con:
            if _connection is None:
                con.execute("BEGIN IMMEDIATE")
            current = con.execute("SELECT * FROM latest_recognition_calls WHERE filename=?",
                                  (filename,)).fetchone()
            # A previous request can finish after a newer request has started.
            # Its result may still be kept, but its trace must not replace the
            # latest request's trace (including its in-progress marker).
            if run_key and current and current["run_key"] != run_key:
                return False
            available = isinstance(snapshot, dict) and isinstance(snapshot.get("calls"), list)
            if not available and not run_key:
                return False
            started_at = ((snapshot.get("started_at") if available else None)
                          or (current["started_at"] if current else trace_timestamp()))
            if not run_key and current and _time(started_at) < _time(current["started_at"]):
                return False
            payload = snapshot if available else {}
            trace_status = str(payload.get("status") or status)
            if trace_status == "completed":
                trace_status = "succeeded"
            con.execute("""INSERT INTO latest_recognition_calls
                (filename,run_key,started_at,status,snapshot_json,result_id)
                VALUES(?,?,?,?,?,?) ON CONFLICT(filename) DO UPDATE SET
                run_key=excluded.run_key,started_at=excluded.started_at,status=excluded.status,
                snapshot_json=excluded.snapshot_json,result_id=excluded.result_id""",
                (filename, run_key, started_at, trace_status,
                 json.dumps(payload, ensure_ascii=False), result_id))
            return True

    def latest_recognition_calls(self, result_id):
        with self.connect() as con:
            result = con.execute("SELECT filename FROM results WHERE id=? AND deleted_at=''",
                                 (result_id,)).fetchone()
            if result is None:
                raise KeyError(result_id)
            row = con.execute("SELECT * FROM latest_recognition_calls WHERE filename=?",
                              (result["filename"],)).fetchone()
            combined = None
            # Earlier versions recorded accepted async requests but no call row.
            # Project that known call without inventing missing local details or timing.
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='danzhengtong_jobs'").fetchone():
                from .async_recognition_calls import combined_remote_trace
                combined = combined_remote_trace(con, result['filename'], row)
                if combined:
                    remote, snapshot = combined
                    row = {**(dict(row) if row is not None else {}), 'snapshot_json': json.dumps(snapshot),
                           'started_at': snapshot.get('started_at', ''),
                           'status': 'succeeded' if snapshot['status'] == 'completed' else snapshot['status'],
                           'result_id': remote['result_id'] or remote['job_result_id'],
                           'job_id': remote['job_id'], 'attempt': remote['attempt']}

            def remote_timings(snapshot):
                from .remote_timings import remote_interface_timings

                identity = (row['result_id'] if row is not None else None) or result_id
                saved = con.execute("SELECT result_json FROM results WHERE id=? AND filename=? AND deleted_at=''",
                                    (identity, result['filename'])).fetchone()
                try:
                    evidence = json.loads(saved['result_json']) if saved else {}
                except (TypeError, json.JSONDecodeError):
                    evidence = {}
                if not isinstance(evidence, dict):
                    evidence = {}
                # A failed/running full retry can still point to the preceding
                # saved result. Its remote response must not become this run's
                # server timing. Incremental sample runs intentionally retain it.
                if (row is not None and row['status'] not in {'succeeded', 'completed'}
                        and not snapshot.get('incremental')
                        and (not row['job_id'] or (evidence.get('queue') or {}).get('job_id') != row['job_id'])):
                    evidence = {}
                return remote_interface_timings(snapshot, evidence)

            output = dict(filename=result["filename"], available=False, status="unavailable",
                          summary=None, calls=[], started_at="", finished_at="",
                          processing_seconds=None, model_initialization_seconds=None,
                          recognition_seconds=None, image_processing=None, result_id=None, remote_timings=[],
                          timeline=None,
                          note="这张回单尚未采集识别调用明细，请重新识别后查看。")
            if row is None:
                output['remote_timings'] = remote_timings({})
                return output
            snapshot = json.loads(row["snapshot_json"])
            available = isinstance(snapshot.get("calls"), list)
            status = row["status"]
            if status == "running" and row["job_id"]:
                job = con.execute("SELECT status,attempt FROM recognition_jobs WHERE id=?",
                                  (row["job_id"],)).fetchone()
                remote_pending = con.execute("""SELECT 1 FROM danzhengtong_jobs WHERE job_id=? AND active=1
                    AND status IN ('queued','submitting','waiting','received')""", (row['job_id'],)).fetchone() if combined else None
                if not remote_pending and (job is None or job["status"] != "running" or job["attempt"] != row["attempt"]):
                    status = "interrupted"
            output.update(available=available, status=status, started_at=row["started_at"],
                          result_id=row["result_id"])
            if available:
                output.update({key: snapshot.get(key) for key in
                               ("summary", "calls", "finished_at", "processing_seconds", "image_processing", "incremental")})
                total = _seconds(snapshot.get('processing_seconds'))
                initialization = _seconds(snapshot.get('model_initialization_seconds'))
                if initialization is None:
                    initialization = _legacy_initialization(con, row, snapshot)
                if total is not None and initialization is not None and initialization <= total + .01:
                    initialization = round(min(total, initialization), 4)
                    output.update(model_initialization_seconds=initialization,
                                  recognition_seconds=round(max(0, total - initialization), 4))
                output.pop("note")
                if snapshot.get('note'):
                    output['note'] = snapshot['note']
            elif status == "running":
                output["note"] = "本次正在识别，完成后可查看实际调用次数和耗时。"
            elif status == "interrupted":
                output["note"] = "最近一次识别已中断，未保存完整调用明细。"
            output['remote_timings'] = remote_timings(snapshot)
            from .run_timeline import recognition_timeline
            output['timeline'] = recognition_timeline(snapshot,
                run_key=row.get('run_key', '') if isinstance(row, dict) else row['run_key'], status=status)
            return output
