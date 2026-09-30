"""Result persistence operations and receipt filtering."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext

from ..domain.documents.pagination import merge_paginated_results, page_group_candidates, page_identity
from ..domain.fields.schema import derive_signature_check
from .database_clock import now_iso


class ResultOperationsMixin:

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
        limit: int | None = None, offset: int = 0, ordering: str = "id",
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
        if ordering == "review":
            completed = {"无需复核", "确认通过", "确认不通过"}

            def review_group(item: dict) -> int:
                if item.get("review_status") in completed:
                    return 0
                if item.get("overall") == "识别失败" or item.get("error_message"):
                    return 2
                return 1

            filtered.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
            filtered.sort(
                key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
                reverse=True,
            )
            filtered.sort(key=review_group)
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
