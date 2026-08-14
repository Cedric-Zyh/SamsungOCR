"""Run every currently annotated sample through the real Flask API.

The module name is retained for compatibility with the original six-sample baseline.
"""

from __future__ import annotations

import json
import argparse
from pathlib import Path

from app import ARTIFACT_DIR, GROUND_TRUTH_PATH, app, database
from receipt_ocr.evaluation import evaluate_results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend",
        choices=("vision", "paddle", "paddle_server", "hybrid", "hybrid_server"),
        default="vision",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="只验收真值文件中的前 N 张；0 表示全部。原始六张基线请使用 --limit 6。",
    )
    parser.add_argument(
        "--sample", action="append", default=[],
        help="只验收指定样单文件名，可重复传入；优先于 --limit。",
    )
    parser.add_argument(
        "--exercise-review", action="store_true",
        help="用真值对第一张执行单张人工复核，并对第二张执行批量确认，验证审计闭环。",
    )
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--excel-path", type=Path, default=None)
    parser.add_argument(
        "--skip-excel", action="store_true",
        help="专项 OCR 探针不生成 Excel；正式端到端验收默认仍会导出。",
    )
    args = parser.parse_args()
    truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    if args.sample:
        missing = [name for name in args.sample if name not in truth]
        if missing:
            raise ValueError(f"样单没有真值：{', '.join(missing)}")
        names = list(dict.fromkeys(args.sample))
    else:
        names = list(truth)
        if args.limit:
            if args.limit < 1:
                raise ValueError("--limit 必须大于 0")
            names = names[:args.limit]
    truth_subset = {name: truth[name] for name in names}
    client = app.test_client()
    task_response = client.post("/api/tasks", json={
        "name": f"{len(names)} 张标注样单验收测试 · {args.backend}",
        "total": len(names),
        "ocr_backend": args.backend,
    })
    if task_response.status_code != 200:
        raise RuntimeError(task_response.get_data(as_text=True))
    task_id = task_response.get_json()["id"]
    records = []
    full_results = []
    for name in names:
        response = client.post("/api/analyze", data={"task_id": task_id, "sample": name})
        payload = response.get_json()
        full_results.append(payload)
        records.append({
            "filename": name,
            "http_status": response.status_code,
            "id": payload.get("id"),
            "overall": payload.get("overall", "识别失败"),
            "review_status": payload.get("review_status", "待复核"),
            "date": payload.get("date_check", {}),
            "seal": payload.get("seal_check", {}),
            "review_reasons": payload.get("review_reasons", []),
            "ocr_backend": payload.get("ocr_backend", ""),
            "ocr_stage_backends": payload.get("ocr_stage_backends", {}),
            "processing_seconds": payload.get("processing_seconds", 0),
            "fields": payload.get("fields", {}),
            "product_table": payload.get("product_table", {}),
            "artifacts": payload.get("processing_artifacts", {}),
            "error": payload.get("error", ""),
        })

    machine_accuracy = evaluate_results(full_results, truth_subset)
    review_audit = {}
    if args.exercise_review and records:
        review_audit = _exercise_review_loop(client, records, truth_subset)

    report_response = client.get("/api/report")
    if report_response.status_code != 200:
        raise RuntimeError(report_response.get_data(as_text=True))
    task = client.get(f"/api/tasks/{task_id}").get_json()
    report = report_response.get_json()

    missing_artifacts = []
    artifact_http_failures = []
    artifact_contract_failures = []
    for record in records:
        date_artifacts = record["artifacts"].get("date", [])
        seal_artifacts = record["artifacts"].get("seals", [])
        if not date_artifacts:
            artifact_contract_failures.append(f"{record['filename']}: 没有手写日期中间图")
        if not seal_artifacts:
            artifact_contract_failures.append(f"{record['filename']}: 没有印章中间图")
        for item in date_artifacts:
            if not item.get("original_url") or not (
                item.get("color_clean_url") or item.get("line_clean_url")
            ):
                artifact_contract_failures.append(
                    f"{record['filename']}: 日期区域缺少原图或处理后图"
                )
            if not item.get("date_line_original_url") or not item.get(
                "date_line_table_clean_url"
            ):
                artifact_contract_failures.append(
                    f"{record['filename']}: 日期行缺少原图或去表格线图"
                )
            if not item.get("date_line_otsu_upscaled_url"):
                artifact_contract_failures.append(
                    f"{record['filename']}: 日期行缺少 Otsu 二值三倍放大图"
                )
        for item in seal_artifacts:
            if not item.get("original_url") or not (
                item.get("isolated_url") or item.get("unwrapped_url")
            ):
                artifact_contract_failures.append(
                    f"{record['filename']}: 印章区域缺少原图或处理后图"
                )
            if item.get("shape") == "圆形" and not item.get(
                "unwrapped_rotated_url"
            ):
                artifact_contract_failures.append(
                    f"{record['filename']}: 圆章缺少 180° 展开复核图"
                )
            if item.get("shape") == "圆形" and not item.get(
                "color_isolated_rotations_url"
            ):
                artifact_contract_failures.append(
                    f"{record['filename']}: 圆章缺少保留章色旋转对照图"
                )
        for group in (date_artifacts, seal_artifacts):
            for artifact in group:
                for key, url in artifact.items():
                    if key.endswith("_urls"):
                        urls = url if isinstance(url, list) else []
                        for item_url in urls:
                            item_url = str(item_url or "").strip()
                            marker = "/files/artifacts/"
                            if marker not in item_url:
                                artifact_contract_failures.append(
                                    f"{record['filename']}: 非法中间图地址 {item_url}"
                                )
                                continue
                            relative = item_url.split(marker, 1)[-1]
                            if not (ARTIFACT_DIR / relative).is_file():
                                missing_artifacts.append(item_url)
                            response = client.get(item_url)
                            if response.status_code != 200:
                                artifact_http_failures.append({
                                    "url": item_url,
                                    "status": response.status_code,
                                })
                        continue
                    if not key.endswith("_url"):
                        continue
                    url = str(url or "").strip()
                    # Some processing variants intentionally have an optional
                    # URL key with an empty value.  Only populated evidence
                    # links belong in the file/HTTP audit.
                    if not url:
                        continue
                    marker = "/files/artifacts/"
                    if marker not in url:
                        artifact_contract_failures.append(
                            f"{record['filename']}: 非法中间图地址 {url}"
                        )
                        continue
                    relative = url.split(marker, 1)[-1]
                    if not (ARTIFACT_DIR / relative).is_file():
                        missing_artifacts.append(url)
                    response = client.get(url)
                    if response.status_code != 200:
                        artifact_http_failures.append({"url": url, "status": response.status_code})

    suffix = f"-{len(names)}" if len(names) != len(truth) else "-all"
    export_path = args.excel_path or Path(
        f"storage/exports/e2e-six-{args.backend}{suffix}.xlsx"
    )
    if args.skip_excel:
        excel_result = {
            "skipped": True,
            "http_status": None,
            "path": "",
            "bytes": 0,
            "error": None,
        }
    else:
        export_response = client.get(
            f"/api/export.xlsx?task_id={task_id}&ocr_backend={args.backend}"
        )
        if export_response.status_code == 200:
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_bytes(export_response.data)
        excel_result = {
            "skipped": False,
            "http_status": export_response.status_code,
            "path": str(export_path.resolve()),
            "bytes": len(export_response.data),
            "error": export_response.get_json(silent=True),
        }

    output = {
        "task": task,
        "backend": args.backend,
        "samples": names,
        "accuracy": machine_accuracy,
        "records": records,
        "review_audit": review_audit,
        "report": report,
        "missing_artifacts": missing_artifacts,
        "artifact_http_failures": artifact_http_failures,
        "artifact_contract_failures": artifact_contract_failures,
        "excel": excel_result,
    }
    result_path = args.report_path or Path(
        f"storage/e2e-six-{args.backend}{suffix}-report.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _exercise_review_loop(client, records: list[dict], truth: dict) -> dict:
    """Exercise editable values, immutable machine output, history, bulk confirmation and filters."""
    # Prefer uncertain records so this proves the actual queue-closing path,
    # rather than merely changing an already-safe automatic pass to confirmed.
    first = next(
        (record for record in records if record.get("review_status") == "待复核"),
        records[0],
    )
    first_truth = truth[first["filename"]]
    result_id = int(first["id"])
    before = client.get(f"/api/results/{result_id}").get_json()
    with database.connect() as connection:
        original_before = connection.execute(
            "SELECT original_result_json FROM results WHERE id=?", (result_id,)
        ).fetchone()["original_result_json"]

    product_edits = []
    for row_index, row in enumerate(first_truth.get("product_rows", [])):
        for column, value in row.items():
            product_edits.append({"row": row_index, "column": column, "value": value})
    seal_text = first_truth["fields"]["签章要求"]
    payload = {
        "fields": first_truth["fields"],
        "product_rows": product_edits,
        "actual_date": first_truth.get("actual_date", ""),
        "seal_text": seal_text,
        "truth_seal_should_match": bool(first_truth.get("seal_should_match")),
        "error_type": "端到端验收",
        "human_note": "六张样单自动化验收：人工值、原始值与历史留痕检查",
        "review_status": "确认通过",
        "final_result": "通过",
        "action": "端到端人工复核",
    }
    response = client.patch(f"/api/results/{result_id}/review", json=payload)
    if response.status_code != 200:
        raise RuntimeError(response.get_data(as_text=True))
    reviewed = response.get_json()
    history = client.get(f"/api/results/{result_id}/review-history").get_json()
    with database.connect() as connection:
        original_after = connection.execute(
            "SELECT original_result_json FROM results WHERE id=?", (result_id,)
        ).fetchone()["original_result_json"]

    bulk = {"skipped": True}
    unsafe_bulk = {"skipped": True}
    remaining = [record for record in records if int(record["id"]) != result_id]
    unsafe_second = next(
        (record for record in remaining if record.get("review_status") == "待复核"),
        None,
    )
    if unsafe_second is not None:
        unsafe_response = client.post("/api/results/bulk-review", json={
            "ids": [int(unsafe_second["id"])],
            "review_status": "确认通过",
            "final_result": "通过",
            "human_note": "该操作应被安全规则拒绝",
        })
        unsafe_bulk = {
            "skipped": False,
            "http_status": unsafe_response.status_code,
            "rejected": unsafe_response.status_code == 409,
            "error": (unsafe_response.get_json(silent=True) or {}).get("error", ""),
        }

    second = next(
        (
            record for record in remaining
            if record.get("overall") == "通过"
            and record.get("date", {}).get("reliable") is True
            and record.get("seal", {}).get("reliable") is True
        ),
        None,
    )
    if second is not None:
        second_id = int(second["id"])
        bulk_response = client.post("/api/results/bulk-review", json={
            "ids": [second_id],
            "review_status": "确认通过",
            "final_result": "通过",
            "human_note": "六张样单自动化验收：批量确认接口",
        })
        if bulk_response.status_code != 200:
            raise RuntimeError(bulk_response.get_data(as_text=True))
        bulk = {
            "skipped": False,
            "http_status": bulk_response.status_code,
            "result_status": bulk_response.get_json()[0].get("review_status"),
        }

    filter_checks = {}
    fields = reviewed.get("fields", {})
    cases = {
        "filename": first["filename"],
        "order_id": fields.get("客户订单号", ""),
        "customer": fields.get("客户名称", ""),
        "date": reviewed.get("date_check", {}).get("actual", ""),
        "overall": "通过",
        "review_status": "确认通过",
    }
    for key, value in cases.items():
        if not value:
            filter_checks[key] = {"skipped": True, "reason": "值为空"}
            continue
        filtered = client.get("/api/results", query_string={key: value, "limit": 2000})
        found_ids = [int(item["id"]) for item in filtered.get_json()]
        filter_checks[key] = {
            "http_status": filtered.status_code,
            "found_reviewed_record": result_id in found_ids,
        }

    return {
        "result_id": result_id,
        "before_review_status": before.get("review_status"),
        "after_review_status": reviewed.get("review_status"),
        "after_final_result": reviewed.get("final_result"),
        "human_note_saved": reviewed.get("human_note") == payload["human_note"],
        "actual_date_saved": reviewed.get("date_check", {}).get("actual") == first_truth.get("actual_date"),
        "seal_text_saved": reviewed.get("seal_check", {}).get("recognized") == seal_text,
        "history_count": len(history),
        "history_action": history[0].get("action") if history else "",
        "immutable_machine_result": original_before == original_after,
        "bulk_confirmation": bulk,
        "unsafe_bulk_confirmation": unsafe_bulk,
        "filters": filter_checks,
    }


if __name__ == "__main__":
    main()
