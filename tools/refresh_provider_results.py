"""Preview or apply saved provider field acceptance and comparison rule updates."""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.database import Database
from receipt_ocr.provider_result_refresh import REFRESH_ACTION, has_human_edits, refresh_provider_result


def refresh_database(path, *, ids=None, apply=False):
    if ids is not None and not ids:
        raise ValueError("指定记录列表不能为空")
    database = Database(path)
    report = {"mode": "apply" if apply else "dry-run", "counts": Counter(), "changes": []}
    # Never initialize the production database as part of a rule refresh.
    connection = sqlite3.connect(Path(path).resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if apply:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
        conditions, parameters = ["deleted_at=''"], []
        if ids:
            conditions.append(f"id IN ({','.join('?' for _ in ids)})")
            parameters.extend(ids)
        rows = connection.execute(f"SELECT * FROM results WHERE {' AND '.join(conditions)} ORDER BY id", parameters).fetchall()
        protected = {row[0] for row in connection.execute(
            "SELECT DISTINCT result_id FROM review_history WHERE action NOT IN (?, '安全规则升级', '重新识别')", (REFRESH_ACTION,))}
        for row in rows:
            current = database._row_to_result(row)
            if row["id"] in protected or has_human_edits(current):
                report["counts"]["human_preserved"] += 1
                continue
            updated = refresh_provider_result(current)
            if updated == current:
                report["counts"]["unchanged"] += 1
                continue
            change = {"id": row["id"], "filename": row["filename"], "stages": {}}
            for stage in ("date", "seal"):
                before, after = current.get(f"{stage}_check") or {}, updated.get(f"{stage}_check") or {}
                if before != after:
                    change["stages"][stage] = {"before": [before.get("status"), before.get("reliable")],
                                                "after": [after.get("status"), after.get("reliable")]}
            if current.get("document_type") != updated.get("document_type"):
                change["stages"]["fields"] = {"before": current.get("document_type"),
                                              "after": updated.get("document_type")}
            if current.get("review_reasons") != updated.get("review_reasons"):
                change["review_reasons"] = {"before": current.get("review_reasons", []),
                                           "after": updated.get("review_reasons", [])}
            if any(current.get(key) != updated.get(key) for key in ("final_result", "review_status")):
                change["verdict"] = {"before": [current.get("final_result"), current.get("review_status")],
                                     "after": [updated.get("final_result"), updated.get("review_status")]}
            report["changes"].append(change)
            report["counts"]["changed"] += 1
            if apply:
                # Rows and human-review history were read after the write lock,
                # so an intervening human edit cannot be overwritten.
                database.review_result(row["id"], result=updated,
                    review_status=updated["review_status"], final_result=updated["final_result"],
                    note=current.get("human_note", ""), error_type=current.get("error_type", ""),
                    action=REFRESH_ACTION, _connection=connection)
        if apply:
            connection.commit()
    finally:
        connection.close()
    report["counts"] = dict(report["counts"])
    return report


def main():
    parser = argparse.ArgumentParser(description="使用已保存的接口结果更新单证通字段采信、日期与印章比对；默认只预览")
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[1] / "storage/results.db")
    parser.add_argument("--ids", nargs="+", type=int, help="只处理指定记录")
    parser.add_argument("--apply", action="store_true", help="保存更新并写入审核历史")
    args = parser.parse_args()
    report = refresh_database(args.database, ids=args.ids, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
