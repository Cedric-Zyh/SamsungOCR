"""Human review and audit persistence operations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext

from ..domain.decision import has_provider_failure
from .constants import REVIEW_STATUSES
from .database_clock import now_iso


class ReviewOperationsMixin:

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
