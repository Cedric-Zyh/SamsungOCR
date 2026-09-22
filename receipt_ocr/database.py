from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .pagination import merge_paginated_results, page_group_candidates, page_identity
from .field_schema import derive_signature_check
from .decision import has_provider_failure


REVIEW_STATUSES = {"无需复核", "待复核", "确认通过", "确认不通过"}
DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

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

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    preview_name TEXT NOT NULL,
                    overall TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(results)")}
            migrations = {
                "deleted_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
                "task_id": "TEXT NOT NULL DEFAULT ''",
                "original_result_json": "TEXT NOT NULL DEFAULT '{}'",
                "review_status": "TEXT NOT NULL DEFAULT '待复核'",
                "final_result": "TEXT NOT NULL DEFAULT ''",
                "human_note": "TEXT NOT NULL DEFAULT ''",
                "error_type": "TEXT NOT NULL DEFAULT ''",
                "error_message": "TEXT NOT NULL DEFAULT ''",
                "attempt": "INTEGER NOT NULL DEFAULT 1",
                "page_group": "TEXT NOT NULL DEFAULT ''",
                "parent_result_id": "INTEGER NOT NULL DEFAULT 0",
                "page_index": "INTEGER NOT NULL DEFAULT 0",
                "page_key": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE results ADD COLUMN {name} {definition}")
            # Filename-derived keys let each new page reconcile only its own
            # receipt. Backfill legacy rows once without decoding OCR payloads.
            for row in connection.execute("SELECT id,filename FROM results WHERE page_key=''").fetchall():
                connection.execute("UPDATE results SET page_key=? WHERE id=?",
                                   (page_identity(row["filename"])[0], row["id"]))

            connection.execute(
                """
                UPDATE results
                SET original_result_json = CASE
                        WHEN original_result_json = '{}' THEN result_json
                        ELSE original_result_json
                    END,
                    updated_at = CASE WHEN updated_at = '' THEN created_at ELSE updated_at END,
                    review_status = CASE
                        WHEN review_status = '' THEN '待复核'
                        ELSE review_status
                    END,
                    final_result = CASE
                        WHEN final_result = '' THEN overall
                        ELSE final_result
                    END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    error_type TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ground_truth_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    result_id INTEGER NOT NULL,
                    changed_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """INSERT OR IGNORE INTO app_settings(key,value,updated_at)
                   VALUES('retention_days',?,?)""",
                (str(DEFAULT_RETENTION_DAYS), now_iso()),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_tasks (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    pending_review INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT ''
                )
                """
            )
            task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(batch_tasks)")}
            if "execution_mode" not in task_columns:
                connection.execute("ALTER TABLE batch_tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'sync'")
            if "recognition_config" not in task_columns:
                connection.execute("ALTER TABLE batch_tasks ADD COLUMN recognition_config TEXT NOT NULL DEFAULT 'null'")
            if "ocr_backend" not in task_columns:
                connection.execute(
                    "ALTER TABLE batch_tasks ADD COLUMN ocr_backend TEXT NOT NULL DEFAULT ''"
                )
            if "seal_recognition_mode" not in task_columns:
                connection.execute(
                    "ALTER TABLE batch_tasks ADD COLUMN seal_recognition_mode TEXT NOT NULL DEFAULT 'local'"
                )
            if "error_message" not in task_columns:
                connection.execute(
                    "ALTER TABLE batch_tasks ADD COLUMN error_message TEXT NOT NULL DEFAULT ''"
                )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_results_filename ON results(filename)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_results_review ON results(review_status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_results_page_group ON results(page_group)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_results_task_page_key ON results(task_id,page_key)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ground_truth_filename ON ground_truth_history(filename)"
            )
            # Older project versions counted pending items only at recognition
            # time. Reconcile from the current result states on startup so a
            # completed human review cannot leave a stale batch badge.
            connection.execute(
                """UPDATE batch_tasks
                SET pending_review=CASE
                    WHEN status='已中断' THEN MAX(
                        failed,
                        (SELECT COUNT(*) FROM results
                         WHERE results.task_id=batch_tasks.id
                           AND results.review_status='待复核')
                    )
                    ELSE (SELECT COUNT(*) FROM results
                          WHERE results.task_id=batch_tasks.id
                            AND results.review_status='待复核')
                END"""
            )

    @staticmethod
    def normalize_retention_days(value: object) -> int:
        """Validate the number of days that should remain in local storage."""
        if isinstance(value, bool):
            raise ValueError("保留天数必须是整数")
        try:
            days = int(value)
        except (TypeError, ValueError):
            raise ValueError("保留天数必须是整数") from None
        if str(value).strip() != str(days):
            raise ValueError("保留天数必须是整数")
        if not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
            raise ValueError(f"保留天数必须在 {MIN_RETENTION_DAYS} 至 {MAX_RETENTION_DAYS} 天之间")
        return days

    def get_retention_days(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key='retention_days'"
            ).fetchone()
        try:
            return self.normalize_retention_days(row["value"] if row else DEFAULT_RETENTION_DAYS)
        except ValueError:
            return DEFAULT_RETENTION_DAYS

    def set_retention_days(self, value: object) -> int:
        days = self.normalize_retention_days(value)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO app_settings(key,value,updated_at) VALUES('retention_days',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (str(days), now_iso()),
            )
        return days

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

    @staticmethod
    def _adjust_task_pending_review(
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

    def insert_result(
        self,
        *,
        filename: str,
        stored_name: str,
        preview_name: str,
        task_id: str,
        result: dict,
        error_type: str = "",
        error_message: str = "",
        _connection: sqlite3.Connection | None = None,
    ) -> int:
        timestamp = result.get("created_at") or now_iso()
        result["created_at"] = timestamp
        review_status = result.get("review_status", "待复核")
        payload = json.dumps(result, ensure_ascii=False)
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO results(
                    created_at,updated_at,filename,stored_name,preview_name,overall,result_json,
                    task_id,original_result_json,review_status,final_result,human_note,error_type,error_message,attempt,page_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    timestamp, timestamp, filename, stored_name, preview_name,
                    result.get("overall", "识别失败"), payload, task_id, payload,
                    review_status, result.get("final_result", result.get("overall", "")), "",
                    error_type, error_message, 1, page_identity(filename)[0],
                ),
            )
            result_id = int(cursor.lastrowid)
            if _connection is None:
                self.reconcile_task_pages(task_id, filename=filename, _connection=connection)
        return result_id

    def reconcile_task_pages(self, task_id: str, *, filename: str | None = None,
                             _connection: sqlite3.Connection | None = None) -> int:
        """Recompute current page relationships, clearing obsolete associations."""
        if not task_id:
            return 0
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            conditions, parameters = ["task_id=?", "deleted_at=''"], [task_id]
            if filename is not None:
                conditions.append("page_key=?")
                parameters.append(page_identity(filename)[0])
            rows = connection.execute(
                f"SELECT * FROM results WHERE {' AND '.join(conditions)} ORDER BY id", parameters
            ).fetchall()
            groups = page_group_candidates([self._row_to_result(row) for row in rows])
            associations = {}
            for group in groups:
                cover = group["cover"]
                cover_id = int(cover["id"])
                continuations = [item for _, item in group["continuations"]]
                info = {
                    "key": group["key"],
                    "page_count": 1 + len(continuations),
                    "cover_result_id": cover_id,
                    "continuation_result_ids": [int(item["id"]) for item in continuations],
                    "filenames": [cover["filename"]] + [item["filename"] for item in continuations],
                }
                group_id = f"{task_id}:{group['key']}"
                for item in [cover] + continuations:
                    item_id = int(item["id"])
                    _, page_index = page_identity(item["filename"])
                    parent_id = 0 if item_id == cover_id else cover_id
                    associations[item_id] = (group_id, parent_id, page_index, info)
            changed = 0
            for row in rows:
                try:
                    original = json.loads(row["result_json"])
                except json.JSONDecodeError:
                    original = {}
                payload = dict(original)
                # These are derived relationships, not the public read model.
                # Persisting _row_to_result() here also copied stale column
                # values into JSON and changed untouched pages on every pass.
                for key in ("page_group", "page_group_id", "page_role", "parent_result_id", "page_index"):
                    payload.pop(key, None)
                group_id, parent_id, page_index, info = associations.get(row["id"], ("", 0, 0, None))
                if info is not None:
                    payload.update(page_group=info, page_role="continuation" if parent_id else "cover",
                                   parent_result_id=parent_id, page_index=page_index)
                if (payload == original and group_id == row["page_group"]
                        and parent_id == row["parent_result_id"] and page_index == row["page_index"]):
                    continue
                connection.execute(
                    """UPDATE results SET result_json=?,page_group=?,parent_result_id=?,page_index=?
                    WHERE id=?""",
                    (json.dumps(payload, ensure_ascii=False), group_id, parent_id, page_index, row["id"]),
                )
                changed += 1
            return changed

    def get_result(self, result_id: int) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM results WHERE id = ? AND deleted_at=''", (result_id,)).fetchone()
        if not row:
            raise KeyError(result_id)
        return self._row_to_result(row)

    def list_results(
        self,
        *,
        limit: int = 200,
        filters: dict | None = None,
        latest_by_filename: bool = False,
    ) -> list[dict]:
        filters = filters or {}
        with self.connect() as connection:
            if latest_by_filename:
                # Scope before decoding large OCR payloads. These columns are
                # authoritative in _row_to_result and already precede dedup.
                conditions = ["deleted_at=''"]
                parameters = []
                for key, expression in (("import_date", "substr(created_at,1,10)"), ("task_id", "task_id")):
                    wanted = str(filters.get(key, "")).strip()
                    if wanted:
                        conditions.append(f"{expression}=?")
                        parameters.append(wanted)
                rows = connection.execute(
                    f"SELECT * FROM results WHERE {' AND '.join(conditions)} ORDER BY id DESC",
                    parameters,
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM results WHERE deleted_at='' ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        items = [self._row_to_result(row) for row in rows]
        if latest_by_filename:
            # Backend, import day and task select the result stream first.  A later Server
            # experiment must not hide the latest production Hybrid result for
            # the same file, and a task query must stay inside that task.
            scope_filters = {
                key: filters.get(key, "") for key in ("ocr_backend", "task_id", "import_date")
            }
            scoped = [item for item in items if _matches_filters(item, scope_filters)]
            seen: set[str] = set()
            current = []
            for item in scoped:
                filename = str(item.get("filename") or "")
                key = filename or f"__result_{item.get('id', '')}"
                if key in seen:
                    continue
                seen.add(key)
                current.append(item)
            items = current
        filtered = [item for item in items if _matches_filters(item, filters)]
        # For current-state queries, filtering must happen before the requested
        # limit. Otherwise many historical reruns of early filenames can starve
        # later current receipts from the main workbench.
        return filtered[:limit]

    def query_receipts(
        self, *, filters: dict | None = None, latest_by_filename: bool = False,
        limit: int | None = None, offset: int = 0,
    ) -> dict:
        """Filter and paginate complete logical receipts, including all pages.

        Import day and task select a result stream. Business filters, backend
        selection and filename deduplication act on merged receipts so neither
        a search nor a page boundary can strip the continuation evidence.
        ``list_results`` remains the physical-row API used by existing tools.
        """
        if type(offset) is not int or offset < 0:
            raise ValueError("分页偏移必须为非负整数")
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("分页数量必须为非负整数")
        filters = filters or {}
        conditions, parameters = ["deleted_at=''"], []
        task_id = str(filters.get("task_id", "")).strip()
        if task_id:
            conditions.append("task_id=?")
            parameters.append(task_id)
        day = str(filters.get("import_date", "")).strip()
        if day:
            # Synchronous legacy imports can cross midnight. Select candidate
            # groups by day, then include every page before testing the logical
            # cover's import date below.
            conditions.append("""(substr(created_at,1,10)=? OR
                (task_id<>'' AND (task_id,page_key) IN
                 (SELECT task_id,page_key FROM results WHERE deleted_at='' AND substr(created_at,1,10)=?)))""")
            parameters.extend([day, day])
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM results WHERE {' AND '.join(conditions)} ORDER BY id DESC", parameters
            ).fetchall()
        physical = [self._row_to_result(row) for row in rows]
        if latest_by_filename:
            # Legacy clients could resubmit a page into the same task. Keep
            # only its current physical version before building a logical
            # receipt, otherwise continuation rows are appended twice.
            backend = str(filters.get("ocr_backend", "")).strip()
            current_pages = {}
            for item in physical:
                key = (item["task_id"], item["filename"]) if item["task_id"] else ("", item["id"])
                previous = current_pages.get(key)
                if previous is None or (backend and previous.get("ocr_backend") != backend
                                        and item.get("ocr_backend") == backend):
                    current_pages[key] = item
            physical = sorted(current_pages.values(), key=lambda item: item["id"], reverse=True)
        items = merge_paginated_results(physical)
        # Select the backend stream before filename dedup, preserving the
        # latest production result even when another backend ran more recently.
        stream_filters = {key: filters.get(key, "") for key in ("ocr_backend", "import_date", "task_id")}
        items = [item for item in items if _matches_filters(item, stream_filters)]
        if latest_by_filename:
            seen, current = set(), []
            for item in items:
                key = str(item.get("filename") or f"__result_{item.get('id', '')}")
                if key not in seen:
                    seen.add(key)
                    current.append(item)
            items = current
        filtered = [item for item in items if _matches_filters(item, filters)]
        return {"items": filtered[offset:] if limit is None else filtered[offset:offset + limit],
                "total": len(filtered)}

    def delete_results(self, ids: list[int]) -> list[int]:
        """Delete selected daily records and their hidden repeats/linked pages."""
        affected = set()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for result_id in ids:
                row = connection.execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone()
                if row is None:
                    raise KeyError(result_id)
                # Older versions could retain a relation after a page was
                # reclassified. Validate it before expanding a destructive act.
                self.reconcile_task_pages(row["task_id"], filename=row["filename"], _connection=connection)
                row = connection.execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone()
                related = connection.execute(
                    "SELECT id FROM results WHERE (filename=? AND substr(created_at,1,10)=?) OR (page_group<>'' AND page_group=?)",
                    (row['filename'], row['created_at'][:10], row['page_group']),
                ).fetchall()
                affected.update(item['id'] for item in related)
            for result_id in affected:
                connection.execute("DELETE FROM review_history WHERE result_id=?", (result_id,))
                connection.execute("DELETE FROM results WHERE id=?", (result_id,))
        return sorted(affected)

    def list_tasks(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM batch_tasks ORDER BY created_at DESC LIMIT 200")]

    def import_date_counts(self, month: str, ocr_backend: str = "") -> dict[str, int]:
        """Count distinct imported filenames per day, before other record filters."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,filename,created_at,result_json FROM results WHERE deleted_at='' AND substr(created_at,1,7)=?",
                (month,),
            ).fetchall()
        days: dict[str, set[str]] = {}
        for row in rows:
            if ocr_backend and json.loads(row["result_json"]).get("ocr_backend", "") != ocr_backend:
                continue
            day = row["created_at"][:10]
            days.setdefault(day, set()).add(row["filename"] or f"__result_{row['id']}")
        return {day: len(names) for day, names in days.items()}

    def list_original_results(
        self, *, limit: int = 2000, completed_tasks_only: bool = False
    ) -> list[dict]:
        """Return immutable first-pass machine output for unbiased accuracy metrics."""
        with self.connect() as connection:
            if completed_tasks_only:
                rows = connection.execute(
                    """SELECT results.* FROM results
                    LEFT JOIN batch_tasks ON results.task_id = batch_tasks.id
                    WHERE results.deleted_at='' AND (results.task_id = '' OR batch_tasks.status = '已完成')
                    ORDER BY results.id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM results WHERE deleted_at='' ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        output = []
        for row in rows:
            try:
                item = json.loads(row["original_result_json"])
            except json.JSONDecodeError:
                item = {}
            item.update(id=row["id"], filename=row["filename"], created_at=row["created_at"])
            output.append(item)
        return output

    def review_result(
        self,
        result_id: int,
        *,
        result: dict,
        review_status: str,
        final_result: str,
        note: str,
        action: str,
        error_type: str = "",
        _connection: sqlite3.Connection | None = None,
    ) -> dict:
        if review_status not in REVIEW_STATUSES:
            raise ValueError("无效复核状态")
        timestamp = now_iso()
        result["review_status"] = review_status
        result["final_result"] = final_result
        result["human_note"] = note
        result["updated_at"] = timestamp
        payload = json.dumps(result, ensure_ascii=False)
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
            if row is None:
                raise KeyError(result_id)
            before = self._row_to_result(row)
            connection.execute(
                """UPDATE results SET updated_at=?,overall=?,result_json=?,review_status=?,final_result=?,
                human_note=?,error_type=? WHERE id=?""",
                (timestamp, result.get("overall", final_result), payload, review_status, final_result, note, error_type, result_id),
            )
            connection.execute(
                """INSERT INTO review_history(result_id,changed_at,action,before_json,after_json,note,error_type)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    result_id, timestamp, action,
                    json.dumps(before, ensure_ascii=False), payload, note, error_type,
                ),
            )
            self._adjust_task_pending_review(
                connection,
                str(before.get("task_id", "")),
                str(before.get("review_status", "")),
                review_status,
                timestamp,
            )
            self.reconcile_task_pages(str(before.get("task_id", "")), filename=before["filename"],
                                      _connection=connection)
            return self._row_to_result(connection.execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone())

    def replace_after_retry(self, result_id: int, result: dict, preview_name: str,
                            *, _connection: sqlite3.Connection | None = None) -> dict:
        with (nullcontext(_connection) if _connection is not None else self.connect()) as connection:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM results WHERE id=? AND deleted_at=''", (result_id,)).fetchone()
            if row is None:
                raise KeyError(result_id)
            before = self._row_to_result(row)
            timestamp = now_iso()
            result["created_at"] = before["created_at"]
            result["updated_at"] = timestamp
            payload = json.dumps(result, ensure_ascii=False)
            connection.execute(
                """UPDATE results SET updated_at=?,preview_name=?,overall=?,result_json=?,review_status=?,
                final_result=?,error_type='',error_message='',attempt=attempt+1 WHERE id=?""",
                (
                    timestamp, preview_name, result["overall"], payload,
                    result["review_status"], result["overall"], result_id,
                ),
            )
            connection.execute(
                """INSERT INTO review_history(result_id,changed_at,action,before_json,after_json,note)
                VALUES(?,?,?,?,?,?)""",
                (result_id, timestamp, "重新识别", json.dumps(before, ensure_ascii=False), payload, ""),
            )
            self._adjust_task_pending_review(
                connection,
                str(before.get("task_id", "")),
                str(before.get("review_status", "")),
                str(result.get("review_status", "待复核")),
                timestamp,
            )
            if _connection is None:
                self.reconcile_task_pages(str(before.get("task_id", "")), filename=before["filename"],
                                          _connection=connection)
            return self._row_to_result(connection.execute("SELECT * FROM results WHERE id=?", (result_id,)).fetchone())

    def history(self, result_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_history WHERE result_id=? ORDER BY id DESC", (result_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def all_history(self, limit: int = 1000) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_ground_truth_change(
        self,
        *,
        filename: str,
        result_id: int,
        before: dict | None,
        after: dict,
        note: str = "",
    ) -> dict:
        timestamp = now_iso()
        action = "更新评测真值" if before is not None else "新增评测真值"
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO ground_truth_history(
                    filename,result_id,changed_at,action,before_json,after_json,note
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    filename, result_id, timestamp, action,
                    json.dumps(before, ensure_ascii=False) if before is not None else "{}",
                    json.dumps(after, ensure_ascii=False), note,
                ),
            )
            row = connection.execute(
                "SELECT * FROM ground_truth_history WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def ground_truth_history(self, filename: str, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM ground_truth_history
                WHERE filename=? ORDER BY id DESC LIMIT ?""",
                (filename, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def enforce_uncertain_review_queue(self) -> int:
        """Repair legacy provider failures and unsafe machine decisions.

        This also migrates records produced by the former policy, which treated
        a reliable date or seal mismatch as an automatic rejection.  A
        mismatch is now evidence for the reviewer and must not remain marked
        as ``无需复核`` unless a human has confirmed the final rejection.
        """
        changed = 0
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM results
                WHERE review_status NOT IN ('确认通过','确认不通过')"""
            ).fetchall()
            for row in rows:
                try:
                    result = json.loads(row["result_json"])
                except json.JSONDecodeError:
                    continue
                provider_evidence = {**result, "error_message": row["error_message"]}
                if has_provider_failure(provider_evidence):
                    if (row["overall"] == "识别失败" and row["final_result"] == "识别失败"
                            and row["review_status"] == "无需复核"):
                        continue
                    before = dict(result)
                    timestamp = now_iso()
                    result.update(
                        overall="识别失败", final_result="识别失败",
                        review_status="无需复核", updated_at=timestamp,
                    )
                    payload = json.dumps(result, ensure_ascii=False)
                    connection.execute(
                        """UPDATE results SET updated_at=?,overall=?,result_json=?,
                        review_status=?,final_result=?,error_type=? WHERE id=?""",
                        (timestamp, "识别失败", payload, "无需复核", "识别失败", "识别失败", row["id"]),
                    )
                    connection.execute(
                        """INSERT INTO review_history(
                            result_id,changed_at,action,before_json,after_json,note,error_type
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            row["id"], timestamp, "失败分类修复",
                            json.dumps(before, ensure_ascii=False), payload,
                            "单证通查询、上传、连接或等待超时异常，转入失败列表并允许重试",
                            "识别失败",
                        ),
                    )
                    self._adjust_task_pending_review(
                        connection, str(row["task_id"]), str(row["review_status"]), "无需复核", timestamp,
                    )
                    changed += 1
                    continue
                date = result.get("date_check") or {}
                seal = result.get("seal_check") or {}
                date_unreliable = date.get("reliable") is False
                seal_unreliable = seal.get("reliable") is False
                date_mismatch = date.get("status") == "不匹配"
                seal_mismatch = seal.get("status") == "不匹配"
                if not (date_unreliable or seal_unreliable or date_mismatch or seal_mismatch):
                    continue
                if row["review_status"] == "待复核" and row["overall"] == "需人工复核":
                    continue
                before = dict(result)
                reasons = list(result.get("review_reasons") or [])
                if date_unreliable and "收货日期无法可靠判断" not in reasons:
                    reasons.append("收货日期无法可靠判断")
                if seal_unreliable and "印章内容无法可靠判断" not in reasons:
                    reasons.append("印章内容无法可靠判断")
                timestamp = now_iso()
                result.update(
                    overall="需人工复核", final_result="需人工复核",
                    review_status="待复核", review_reasons=reasons,
                    updated_at=timestamp,
                )
                payload = json.dumps(result, ensure_ascii=False)
                connection.execute(
                    """UPDATE results SET updated_at=?,overall=?,result_json=?,
                    review_status=?,final_result=? WHERE id=?""",
                    (timestamp, "需人工复核", payload, "待复核", "需人工复核", row["id"]),
                )
                connection.execute(
                    """INSERT INTO review_history(
                        result_id,changed_at,action,before_json,after_json,note,error_type
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        row["id"], timestamp, "安全规则升级",
                        json.dumps(before, ensure_ascii=False), payload,
                        "日期或印章不一致需人工确认，或证据不可靠，转入待人工复核",
                        "核验不一致或证据可靠性不足",
                    ),
                )
                self._adjust_task_pending_review(
                    connection,
                    str(row["task_id"]),
                    str(row["review_status"]),
                    "待复核",
                    timestamp,
                )
                changed += 1
        return changed

    def _row_to_result(self, row: sqlite3.Row) -> dict:
        try:
            item = json.loads(row["result_json"])
        except json.JSONDecodeError:
            item = {}
        item.update(
            id=row["id"], filename=row["filename"], stored_name=row["stored_name"],
            preview_name=row["preview_name"], created_at=row["created_at"], updated_at=row["updated_at"],
            overall=row["overall"], review_status=row["review_status"], final_result=row["final_result"],
            human_note=row["human_note"], error_type=row["error_type"], error_message=row["error_message"],
            attempt=row["attempt"], task_id=row["task_id"], page_group_id=row["page_group"],
            parent_result_id=row["parent_result_id"], page_index=row["page_index"],
        )
        item["signature_check"] = derive_signature_check(
            item.get("fields") or {}, item.get("signature_check")
        )
        return item


def _matches_filters(item: dict, filters: dict) -> bool:
    fields = {**item.get("internal_fields", {}), **item.get("fields", {})}
    filenames = list(dict.fromkeys([str(item.get("filename") or ""),
                                   *(str(name) for name in item.get("source_filenames") or [])]))
    search_prefix = str(filters.get('search_prefix') or '').strip().lower()
    if search_prefix:
        candidates = [*filenames, *(name.replace('\\', '/').rsplit('/', 1)[-1] for name in filenames),
                      *(fields.get(name, '') for name in ('客户订单号', '运单号', '销售订单号', '手工订单号'))]
        if not any(str(value).strip().lower().startswith(search_prefix) for value in candidates):
            return False
    contains = {
        "filename": " ".join(filenames),
        "order_id": " ".join(str(fields.get(key, "")) for key in ("客户订单号", "运单号", "销售订单号", "手工订单号")),
        "customer": fields.get("客户名称", ""),
    }
    search = str(filters.get("search") or "").strip().casefold()
    if search and not any(search in str(value).casefold() for value in contains.values()):
        return False
    for key, value in contains.items():
        wanted = str(filters.get(key, "")).strip().lower()
        if wanted and filters.get('text_match') == 'prefix' and key in {'filename', 'order_id'}:
            values = filenames if key == 'filename' else [fields.get(name, '') for name in ('客户订单号', '运单号', '销售订单号', '手工订单号')]
            if not any(str(candidate).strip().lower().startswith(wanted) for candidate in values):
                return False
            continue
        if wanted and wanted not in str(value).lower():
            return False
    for key in ('date', 'seal'):
        wanted = filters.get(f'{key}_status', '')
        check = item.get(f'{key}_check') or {}
        status = check.get('status') or '未识别'
        if status == '匹配' and not check.get('reliable'):
            status = '匹配待确认'
        if wanted and wanted != status:
            return False
    exact = {
        "import_date": str(item.get("created_at") or "")[:10],
        "date": item.get("date_check", {}).get("actual", ""),
        "overall": item.get("final_result") or item.get("overall", ""),
        "review_status": item.get("review_status", ""),
        "task_id": item.get("task_id", ""),
        "ocr_backend": item.get("ocr_backend", ""),
    }
    for key, value in exact.items():
        wanted = str(filters.get(key, "")).strip()
        if wanted and wanted != str(value):
            return False
    return True
