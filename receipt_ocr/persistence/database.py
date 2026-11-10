from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .tasks import TaskOperationsMixin
from .results import ResultOperationsMixin, _matches_filters
from .reviews import ReviewOperationsMixin
from .statistics import StatisticsMixin
from .settings import SettingsMixin
from .seal_standards import SealStandardOperationsMixin
from .recognition_calls import RecognitionCallOperationsMixin
from .schema import initialize_database
from .constants import (
    DEFAULT_RECOGNITION_SETTINGS,
    DEFAULT_RETENTION_DAYS,
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    REVIEW_STATUSES,
)
from .database_clock import now_iso
from .result_writes import ResultWrites


_ACTIVE_REMOTE_SQL = """active=1 AND (
    status IN ('queued','submitting','waiting','received','unknown','conflict')
    OR (status='applied' AND EXISTS (
        SELECT 1 FROM json_each(danzhengtong_jobs.seal_requests_json, '$.requests') child
        WHERE json_extract(child.value, '$.image_download.status') IN ('queued','running'))))"""


class Database(TaskOperationsMixin, ResultOperationsMixin, ReviewOperationsMixin, StatisticsMixin, SettingsMixin, SealStandardOperationsMixin, RecognitionCallOperationsMixin):
    def initialize(self) -> None:
        initialize_database(self)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.result_writes = ResultWrites(self)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()



    def purge_expired(self, retention_days: object | None = None, *, now: datetime | None = None) -> dict:
        """Remove rows older than the configured window.

        Active queue jobs keep their target result alive so a worker cannot
        finish into a record that was deleted while it was processing.
        ``records`` contains the removed rows' storage names for the caller to
        clean up previews, uploads and artifact directories.
        """
        days = self.get_retention_days() if retention_days is None else self.normalize_retention_days(retention_days)
        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.astimezone()
        cutoff = current - timedelta(days=days)
        cutoff_iso = cutoff.isoformat(timespec="seconds")
        removed_records = []
        removed_jobs = 0
        removed_tasks = 0
        with self.connect() as connection:
            # Queue admission and retention must see one protected snapshot.
            # A deferred transaction started by DELETE would let a retry be
            # admitted after the protection checks but before its target goes.
            connection.execute("BEGIN IMMEDIATE")
            active_result_ids = set()
            has_jobs = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_jobs'"
            ).fetchone()
            has_job_history = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_job_history'"
            ).fetchone()
            if has_jobs:
                active_result_ids = {
                    value for row in connection.execute(
                        """SELECT result_id,target_result_id FROM recognition_jobs
                           WHERE status IN ('queued','running')"""
                    )
                    for value in (row["result_id"], row["target_result_id"])
                    if value
                }
            has_remote = connection.execute("SELECT 1 FROM sqlite_master WHERE name='danzhengtong_jobs'").fetchone()
            if has_remote:
                active_result_ids.update(row[0] for row in connection.execute(f"""SELECT result_id FROM danzhengtong_jobs
                    WHERE {_ACTIVE_REMOTE_SQL} AND result_id IS NOT NULL"""))
            rows = connection.execute(
                """SELECT id,stored_name,preview_name,result_json
                   FROM results WHERE julianday(created_at) < julianday(?) ORDER BY id""", (cutoff_iso,)
            ).fetchall()
            for row in rows:
                if row["id"] in active_result_ids:
                    continue
                removed_records.append(dict(row))
            ids = [row["id"] for row in removed_records]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(f"DELETE FROM results WHERE id IN ({placeholders})", ids)
            if has_jobs:
                # Queue history is operational data tied to the task. Keep
                # running work and retries that still target a live result;
                # remove completed or abandoned jobs beyond the same age.
                old_jobs = connection.execute(
                    """SELECT id FROM recognition_jobs
                       WHERE julianday(created_at) < julianday(?) AND status <> 'running'
                         AND NOT (status='queued' AND COALESCE(target_result_id,result_id) IS NOT NULL)""",
                    (cutoff_iso,),
                ).fetchall()
                job_ids = [row["id"] for row in old_jobs]
                if has_remote:
                    protected = {row[0] for row in connection.execute(f"""SELECT job_id FROM danzhengtong_jobs
                        WHERE {_ACTIVE_REMOTE_SQL}""")}
                    job_ids = [job_id for job_id in job_ids if job_id not in protected]
                if job_ids:
                    placeholders = ",".join("?" for _ in job_ids)
                    if has_job_history:
                        connection.execute(
                            f"DELETE FROM recognition_job_history WHERE job_id IN ({placeholders})", job_ids
                        )
                    connection.execute(
                        f"DELETE FROM recognition_jobs WHERE id IN ({placeholders})", job_ids
                    )
                    removed_jobs = len(job_ids)
                old_tasks = connection.execute(
                    """SELECT id FROM batch_tasks WHERE julianday(created_at) < julianday(?)
                       AND NOT EXISTS (SELECT 1 FROM recognition_jobs WHERE task_id=batch_tasks.id)""",
                    (cutoff_iso,),
                ).fetchall()
                task_ids = [row["id"] for row in old_tasks]
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    connection.execute(f"DELETE FROM batch_tasks WHERE id IN ({placeholders})", task_ids)
                    removed_tasks = len(task_ids)
        return {
            "retention_days": days,
            "cutoff": cutoff_iso,
            "records": removed_records,
            "deleted_results": len(removed_records),
            "deleted_jobs": removed_jobs,
            "deleted_tasks": removed_tasks,
        }
