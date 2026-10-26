"""The latest physical recognition calls, stored once per complete filename."""

from contextlib import nullcontext
from datetime import datetime
import json


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


class RecognitionCallOperationsMixin:
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
            output = dict(filename=result["filename"], available=False, status="unavailable",
                          summary=None, calls=[], started_at="", finished_at="",
                          processing_seconds=None, result_id=None,
                          note="这张回单尚未采集识别调用明细，请重新识别后查看。")
            if row is None:
                return output
            snapshot = json.loads(row["snapshot_json"])
            available = isinstance(snapshot.get("calls"), list)
            status = row["status"]
            if status == "running" and row["job_id"]:
                job = con.execute("SELECT status,attempt FROM recognition_jobs WHERE id=?",
                                  (row["job_id"],)).fetchone()
                if job is None or job["status"] != "running" or job["attempt"] != row["attempt"]:
                    status = "interrupted"
            output.update(available=available, status=status, started_at=row["started_at"],
                          result_id=row["result_id"])
            if available:
                output.update({key: snapshot.get(key) for key in
                               ("summary", "calls", "finished_at", "processing_seconds")})
                output.pop("note")
            elif status == "running":
                output["note"] = "本次正在识别，完成后可查看实际调用次数和耗时。"
            elif status == "interrupted":
                output["note"] = "最近一次识别已中断，未保存完整调用明细。"
            return output
