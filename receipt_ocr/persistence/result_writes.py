"""Result write use cases with explicit machine-value and audit semantics.

The database facade supplies connections and read projections. Queue owners
retain their job-state transaction; these operations join that transaction and
keep result audits, review counters and page associations together.
"""
from contextlib import contextmanager
import json

from ..domain.documents.pagination import page_identity
from .constants import REVIEW_STATUSES
from .database_clock import now_iso
from .recognition_calls import take_recognition_calls


class ResultWrites:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def _transaction(self, connection):
        if connection is not None:
            yield connection
        else:
            with self.database.connect() as owned:
                owned.execute('BEGIN IMMEDIATE')
                yield owned

    def _current(self, connection, result_id):
        row = connection.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
        if row is None:
            raise KeyError(result_id)
        return self.database._row_to_result(row)

    @staticmethod
    def _audit(connection, result_id, before, payload, *, timestamp, action, note, error_type=''):
        connection.execute('''INSERT INTO review_history
            (result_id,changed_at,action,before_json,after_json,note,error_type) VALUES(?,?,?,?,?,?,?)''',
            (result_id, timestamp, action, json.dumps(before, ensure_ascii=False), payload, note, error_type))

    def _finish_change(self, connection, before, review_status, timestamp):
        self.database._adjust_task_pending_review(connection, str(before.get('task_id', '')),
            str(before.get('review_status', '')), review_status, timestamp)
        self.database.reconcile_task_pages(str(before.get('task_id', '')),
            filename=before['filename'], _connection=connection)

    def create_recognition(self, *, filename, stored_name, preview_name, task_id, result,
                           error_type='', error_message='', connection=None):
        """Create the first machine result and its immutable evaluation baseline."""
        result = dict(result)
        timestamp = result.get('created_at') or now_iso()
        trace, trace_key = take_recognition_calls(result)
        result['created_at'] = timestamp
        review_status = result.get('review_status', '待复核')
        payload = json.dumps(result, ensure_ascii=False)
        with self._transaction(connection) as con:
            cursor = con.execute('''INSERT INTO results(
                created_at,updated_at,filename,stored_name,preview_name,overall,result_json,
                task_id,original_result_json,review_status,final_result,human_note,error_type,error_message,attempt,page_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (timestamp, timestamp, filename, stored_name, preview_name,
                 result.get('overall', '识别失败'), payload, task_id, payload, review_status,
                 result.get('final_result', result.get('overall', '')), '', error_type, error_message,
                 1, page_identity(filename)[0]))
            result_id = int(cursor.lastrowid)
            self.database.save_recognition_calls(filename, trace, result_id=result_id, run_key=trace_key,
                status='failed' if error_message else 'succeeded', _connection=con)
            self.database.reconcile_task_pages(task_id, filename=filename, _connection=con)
            return result_id

    def replace_recognition(self, result_id, result, preview_name, *, connection=None):
        """Replace a retry's current output while preserving its first machine baseline."""
        result = dict(result)
        trace, trace_key = take_recognition_calls(result)
        with self._transaction(connection) as con:
            before = self._current(con, result_id)
            timestamp = now_iso()
            result.update(created_at=before['created_at'], updated_at=timestamp)
            payload = json.dumps(result, ensure_ascii=False)
            con.execute('''UPDATE results SET updated_at=?,preview_name=?,overall=?,result_json=?,review_status=?,
                final_result=?,error_type='',error_message='',attempt=attempt+1 WHERE id=?''',
                (timestamp, preview_name, result['overall'], payload,
                 result['review_status'], result['overall'], result_id))
            self._audit(con, result_id, before, payload, timestamp=timestamp, action='重新识别', note='')
            self.database.save_recognition_calls(before['filename'], trace, result_id=result_id,
                run_key=trace_key, _connection=con)
            self._finish_change(con, before, str(result.get('review_status', '待复核')), timestamp)
            return self._current(con, result_id)

    def save_review(self, result_id, *, result, review_status, final_result, note, action,
                    error_type='', connection=None):
        """Save reviewed current values and audit; never rewrite the machine baseline."""
        if review_status not in REVIEW_STATUSES:
            raise ValueError('无效复核状态')
        timestamp = now_iso()
        # Preserve the facade's existing mutation contract for review callers.
        result.update(review_status=review_status, final_result=final_result,
                      human_note=note, updated_at=timestamp)
        payload = json.dumps(result, ensure_ascii=False)
        with self._transaction(connection) as con:
            before = self._current(con, result_id)
            con.execute('''UPDATE results SET updated_at=?,overall=?,result_json=?,review_status=?,final_result=?,
                human_note=?,error_type=? WHERE id=?''',
                (timestamp, result.get('overall', final_result), payload, review_status,
                 final_result, note, error_type, result_id))
            self._audit(con, result_id, before, payload, timestamp=timestamp,
                        action=action, note=note, error_type=error_type)
            self._finish_change(con, before, review_status, timestamp)
            return self._current(con, result_id)

    def apply_remote_result(self, result_id, result, *, connection=None):
        """Complete asynchronous machine evidence, including its evaluation baseline.

        The queue owner checks revision/lease eligibility before calling this.
        Manual confirmations therefore remain under the existing conflict guard.
        """
        with self._transaction(connection) as con:
            before = self._current(con, result_id)
            result.update(created_at=before['created_at'], updated_at=now_iso())
            self.save_review(result_id, result=result,
                review_status=result.get('review_status', '待复核'), final_result=result.get('overall', '待复核'),
                note=before['human_note'], action='取回单证通结果', connection=con)
            con.execute("UPDATE results SET original_result_json=?,error_type='',error_message='' WHERE id=?",
                        (json.dumps(result, ensure_ascii=False), result_id))
            return self._current(con, result_id)

    def restore_retained_result(self, result_id, result, preview_name, *, connection=None):
        """Restore already-saved provider evidence for an incremental run.

        This is neither a fresh recognition nor a human review: preserve the
        existing baseline, timestamps, attempt count and audit history.
        """
        with self._transaction(connection) as con:
            self._current(con, result_id)
            con.execute('''UPDATE results SET result_json=?,overall=?,final_result=?,review_status=?,preview_name=?
                WHERE id=? AND deleted_at='' ''',
                (json.dumps(result, ensure_ascii=False), result.get('overall', '需人工复核'),
                 result.get('final_result', result.get('overall', '需人工复核')),
                 result.get('review_status', '待复核'), preview_name, result_id))
            return self._current(con, result_id)

    def repair_saved_result(self, result_id, *, result, review_status, final_result, action,
                            original_result=None, connection=None):
        """Audit an explicit historical correction while retaining human metadata.

        A repair preserves the first machine value unless the caller supplies
        its separately corrected original result. Job snapshots remain owned
        by the repair's queue transaction.
        """
        with self._transaction(connection) as con:
            before = self._current(con, result_id)
            self.save_review(result_id, result=result, review_status=review_status, final_result=final_result,
                note=before['human_note'], error_type=before['error_type'], action=action, connection=con)
            if original_result is not None:
                con.execute('UPDATE results SET original_result_json=? WHERE id=?',
                            (json.dumps(original_result, ensure_ascii=False), result_id))
            return self._current(con, result_id)
