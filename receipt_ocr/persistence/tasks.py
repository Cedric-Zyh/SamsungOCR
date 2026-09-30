"""Batch-task persistence operations."""

from __future__ import annotations

import json

from .database_clock import now_iso


class TaskOperationsMixin:

    def create_task(
        self, task_id: str, name: str, total: int, ocr_backend: str = "",
        seal_recognition_mode: str = "local", recognition_config: dict | None = None,
    ) -> dict:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO batch_tasks(
                    id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode,recognition_config
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    task_id, name, timestamp, timestamp, "处理中", total,
                    ocr_backend, seal_recognition_mode, json.dumps(recognition_config),
                ),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM batch_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise KeyError(task_id)
        return dict(row)

    def recover_interrupted_tasks(self) -> int:
        """Close batches left running by a killed/restarted local process.

        Recognition requests are synchronous within one local app process. If
        that process is starting now, any persisted ``处理中`` task belongs to
        the previous process and cannot resume. Count its remaining slots as
        failures/review items instead of leaving a permanent fake progress
        bar or admitting a partial batch into accuracy reports.
        """
        timestamp = now_iso()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM batch_tasks WHERE status='处理中' AND execution_mode='sync'"
            ).fetchall()
            for row in rows:
                remaining = max(0, int(row["total"]) - int(row["completed"]))
                connection.execute(
                    """UPDATE batch_tasks
                    SET updated_at=?,status='已中断',completed=total,
                        failed=failed+?,pending_review=pending_review+?,
                        error_message='上次本地进程异常退出；未完成项已计为失败，请重新导入或重试'
                    WHERE id=?""",
                    (timestamp, remaining, remaining, row["id"]),
                )
        return len(rows)

    def update_task(self, task_id: str, *, success: bool, pending_review: bool) -> dict:
        timestamp = now_iso()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM batch_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            # A late worker can finish after a restarted app has already
            # recovered its batch as interrupted.  Do not let that stale
            # worker revive the task or push counters beyond ``total``.
            if row["status"] != "处理中":
                return dict(row)
            completed = min(row["total"], row["completed"] + 1)
            succeeded = row["succeeded"] + (1 if success else 0)
            failed = row["failed"] + (0 if success else 1)
            pending = row["pending_review"] + (1 if pending_review else 0)
            status = "已完成" if completed >= row["total"] else "处理中"
            connection.execute(
                """UPDATE batch_tasks SET updated_at=?,status=?,completed=?,succeeded=?,failed=?,pending_review=?
                WHERE id=?""",
                (timestamp, status, completed, succeeded, failed, pending, task_id),
            )
        return self.get_task(task_id)

    def _adjust_task_pending_review(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        before_status: str,
        after_status: str,
        timestamp: str,
    ) -> None:
        """Keep a completed batch's pending count in sync with later reviews."""
        if not task_id:
            return
        delta = int(after_status == "待复核") - int(before_status == "待复核")
        if not delta:
            return
        connection.execute(
            """UPDATE batch_tasks
            SET updated_at=?, pending_review=MAX(0, pending_review + ?)
            WHERE id=?""",
            (timestamp, delta, task_id),
        )

    def list_tasks(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM batch_tasks ORDER BY created_at DESC LIMIT 200")]
