from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .pagination import page_group_candidates, page_identity


REVIEW_STATUSES = {"无需复核", "待复核", "确认通过", "确认不通过"}


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
            }
            for name, definition in migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE results ADD COLUMN {name} {definition}")

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

    def create_task(
        self, task_id: str, name: str, total: int, ocr_backend: str = "",
        seal_recognition_mode: str = "local",
    ) -> dict:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO batch_tasks(
                    id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    task_id, name, timestamp, timestamp, "处理中", total,
                    ocr_backend, seal_recognition_mode,
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
                "SELECT * FROM batch_tasks WHERE status='处理中'"
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
    ) -> int:
        timestamp = result.get("created_at") or now_iso()
        result["created_at"] = timestamp
        review_status = result.get("review_status", "待复核")
        payload = json.dumps(result, ensure_ascii=False)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO results(
                    created_at,updated_at,filename,stored_name,preview_name,overall,result_json,
                    task_id,original_result_json,review_status,final_result,human_note,error_type,error_message,attempt
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    timestamp, timestamp, filename, stored_name, preview_name,
                    result.get("overall", "识别失败"), payload, task_id, payload,
                    review_status, result.get("final_result", result.get("overall", "")), "",
                    error_type, error_message, 1,
                ),
            )
            result_id = int(cursor.lastrowid)
        self.reconcile_task_pages(task_id)
        return result_id

    def reconcile_task_pages(self, task_id: str) -> int:
        """Persist cover/continuation relationships for one batch task."""
        if not task_id:
            return 0
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM results WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
            groups = page_group_candidates([self._row_to_result(row) for row in rows])
            changed = 0
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
                    payload = dict(item)
                    payload["page_group"] = info
                    payload["page_role"] = "cover" if not parent_id else "continuation"
                    payload["parent_result_id"] = parent_id
                    payload["page_index"] = page_index
                    connection.execute(
                        """UPDATE results SET result_json=?,page_group=?,parent_result_id=?,page_index=?
                        WHERE id=?""",
                        (json.dumps(payload, ensure_ascii=False), group_id, parent_id, page_index, item_id),
                    )
                    changed += 1
            return changed

    def get_result(self, result_id: int) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
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
        with self.connect() as connection:
            if latest_by_filename:
                rows = connection.execute(
                    "SELECT * FROM results ORDER BY id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        items = [self._row_to_result(row) for row in rows]
        filters = filters or {}
        if latest_by_filename:
            # Backend and task select the result stream first.  A later Server
            # experiment must not hide the latest production Hybrid result for
            # the same file, and a task query must stay inside that task.
            scope_filters = {
                key: filters.get(key, "") for key in ("ocr_backend", "task_id")
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

    def list_original_results(
        self, *, limit: int = 2000, completed_tasks_only: bool = False
    ) -> list[dict]:
        """Return immutable first-pass machine output for unbiased accuracy metrics."""
        with self.connect() as connection:
            if completed_tasks_only:
                rows = connection.execute(
                    """SELECT results.* FROM results
                    LEFT JOIN batch_tasks ON results.task_id = batch_tasks.id
                    WHERE results.task_id = '' OR batch_tasks.status = '已完成'
                    ORDER BY results.id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)
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
    ) -> dict:
        if review_status not in REVIEW_STATUSES:
            raise ValueError("无效复核状态")
        before = self.get_result(result_id)
        timestamp = now_iso()
        result["review_status"] = review_status
        result["final_result"] = final_result
        result["human_note"] = note
        result["updated_at"] = timestamp
        payload = json.dumps(result, ensure_ascii=False)
        with self.connect() as connection:
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
        self.reconcile_task_pages(str(before.get("task_id", "")))
        return self.get_result(result_id)

    def replace_after_retry(self, result_id: int, result: dict, preview_name: str) -> dict:
        before = self.get_result(result_id)
        timestamp = now_iso()
        result["created_at"] = before["created_at"]
        result["updated_at"] = timestamp
        payload = json.dumps(result, ensure_ascii=False)
        with self.connect() as connection:
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
        self.reconcile_task_pages(str(before.get("task_id", "")))
        return self.get_result(result_id)

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
        """Move explicitly unreliable, unconfirmed machine decisions into review."""
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
                date = result.get("date_check") or {}
                seal = result.get("seal_check") or {}
                date_unreliable = date.get("reliable") is False
                seal_unreliable = seal.get("reliable") is False
                if not (date_unreliable or seal_unreliable):
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
                        "日期或印章证据不可靠，转入待人工复核", "证据可靠性不足",
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
        return item


def _matches_filters(item: dict, filters: dict) -> bool:
    fields = item.get("fields", {})
    contains = {
        "filename": item.get("filename", ""),
        "order_id": " ".join(str(fields.get(key, "")) for key in ("客户订单号", "运单号", "销售订单号", "手工订单号")),
        "customer": fields.get("客户名称", ""),
    }
    for key, value in contains.items():
        wanted = str(filters.get(key, "")).strip().lower()
        if wanted and wanted not in str(value).lower():
            return False
    exact = {
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
