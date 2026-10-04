"""SQLite schema creation and migrations for the receipt database."""

from __future__ import annotations

import json

from ..domain.documents.pagination import page_identity
from .constants import DEFAULT_RECOGNITION_SETTINGS, DEFAULT_RETENTION_DAYS
from .database_clock import now_iso
from .read_model import initialize_read_model
from .issue_migration import migrate_saved_issues


def initialize_database(database) -> None:
        database.path.parent.mkdir(parents=True, exist_ok=True)
        with database.connect() as connection:
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
                """INSERT OR IGNORE INTO app_settings(key,value,updated_at)
                   VALUES('recognition_defaults',?,?)""",
                (json.dumps(DEFAULT_RECOGNITION_SETTINGS, ensure_ascii=False, separators=(",", ":")), now_iso()),
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
            initialize_read_model(connection)
            migrate_saved_issues(connection)
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
