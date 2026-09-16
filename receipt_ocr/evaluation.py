from __future__ import annotations

import json
import threading
from collections import Counter
from pathlib import Path

from .ocr_backends import backend_label
from .parser import PRODUCT_COLUMNS, normalize_text
from .field_schema import OUTPUT_FIELDS


GROUND_TRUTH_FIELDS = (
    "承运商", "运单号", "制单日期", "供应商", "客户名称",
    "客户订单号", "销售订单号", "要求到货", "发货单位", "签章要求",
)
_GROUND_TRUTH_LOCK = threading.Lock()


def load_ground_truth(path: str | Path) -> dict:
    truth_path = Path(path)
    if not truth_path.exists():
        return {}
    return json.loads(truth_path.read_text(encoding="utf-8"))


def build_ground_truth_entry(
    result: dict, *, seal_should_match: bool, date_present: bool = True
) -> dict:
    """Build a strict, reviewable truth row from the current human-confirmed result."""
    fields = result.get("fields") or {}
    names = OUTPUT_FIELDS if result.get("field_schema_version") else GROUND_TRUTH_FIELDS
    truth_fields = {name: str(fields.get(name, "")).strip() for name in names}
    required = {"客户名称", "要求到货", "签章要求"} if result.get("field_schema_version") else set(names)
    missing = [name for name, value in truth_fields.items() if name in required and not value]
    if missing:
        raise ValueError(f"评测真值缺少字段：{'、'.join(missing)}")

    date_check = result.get("date_check") or {}
    actual_date = str(date_check.get("actual", "")).strip()
    if date_present and not actual_date:
        raise ValueError("评测真值必须填写实际收货日期")
    if not date_present and actual_date:
        raise ValueError("日期栏标记为空时，实际收货日期也必须为空")

    product_rows = []
    for index, detail in enumerate((result.get("product_table") or {}).get("rows") or [], start=1):
        values = detail.get("values") or {}
        row = {name: str(values.get(name, "")).strip() for name in PRODUCT_COLUMNS}
        row_missing = [name for name, value in row.items() if not value]
        if row_missing:
            raise ValueError(f"商品第 {index} 行真值缺少：{'、'.join(row_missing)}")
        product_rows.append(row)
    if not product_rows and not result.get("field_schema_version"):
        raise ValueError("评测真值至少需要一行商品明细")

    return {
        "fields": truth_fields,
        "product_rows": product_rows,
        "actual_date": actual_date,
        "date_present": bool(date_present),
        "seal_should_match": bool(seal_should_match),
    }


def save_ground_truth_entry(path: str | Path, filename: str, entry: dict) -> dict:
    """Atomically upsert one sample so interrupted writes never corrupt the truth set."""
    truth_path = Path(path)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    with _GROUND_TRUTH_LOCK:
        truth = load_ground_truth(truth_path)
        before = truth.get(filename)
        truth[filename] = entry
        temporary = truth_path.with_suffix(truth_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(truth_path)
    return {"filename": filename, "before": before, "after": entry, "total": len(truth)}


def evaluate_results(results: list[dict], ground_truth: dict) -> dict:
    latest: dict[str, dict] = {}
    for item in sorted(results, key=lambda row: row.get("id", 0)):
        if item.get("filename") in ground_truth:
            latest[item["filename"]] = item

    field_total = field_correct = 0
    field_stats: dict[str, Counter] = {}
    date_total = date_correct = date_detected = 0
    date_present_total = date_present_detected = 0
    date_absent_total = date_absent_correct = 0
    date_decision_total = date_decision_correct = 0
    seal_total = seal_correct = 0
    seal_decision_total = seal_decision_correct = 0
    seal_scores: list[float] = []
    product_cell_total = product_cell_correct = 0
    product_row_total = product_row_correct = 0
    product_stats: dict[str, Counter] = {}
    samples = []
    for filename, truth in ground_truth.items():
        result = latest.get(filename)
        if not result:
            samples.append({"filename": filename, "status": "未测试", "issues": ["没有识别记录"]})
            continue
        issues = []
        fields = result.get("fields", {})
        for name, expected in truth.get("fields", {}).items():
            if result.get("field_schema_version") and name not in OUTPUT_FIELDS:
                continue
            field_total += 1
            field_stats.setdefault(name, Counter())["total"] += 1
            actual = fields.get(name, "")
            correct = normalize_text(str(actual)) == normalize_text(str(expected))
            if correct:
                field_correct += 1
                field_stats[name]["correct"] += 1
            else:
                issues.append(f"字段错误：{name}")

        expected_product_rows = truth.get("product_rows", [])
        actual_product_rows = [
            row.get("values", {}) for row in result.get("product_table", {}).get("rows", [])
        ]
        actual_by_number = {
            normalize_text(str(row.get("行号", ""))): row
            for row in actual_product_rows if row.get("行号")
        }
        expected_numbers = set()
        for expected_row in expected_product_rows:
            row_number = normalize_text(str(expected_row.get("行号", "")))
            expected_numbers.add(row_number)
            actual_row = actual_by_number.get(row_number, {})
            row_matches = True
            for column in PRODUCT_COLUMNS:
                if column not in expected_row:
                    continue
                product_cell_total += 1
                product_stats.setdefault(column, Counter())["total"] += 1
                expected_value = normalize_text(str(expected_row.get(column, "")))
                actual_value = normalize_text(str(actual_row.get(column, "")))
                if actual_value == expected_value:
                    product_cell_correct += 1
                    product_stats[column]["correct"] += 1
                else:
                    row_matches = False
                    issues.append(f"商品明细错误：第{expected_row.get('行号', '?')}行{column}")
            product_row_total += 1
            if row_matches:
                product_row_correct += 1
        extra_rows = [
            row for row in actual_product_rows
            if normalize_text(str(row.get("行号", ""))) not in expected_numbers
        ]
        if expected_product_rows and extra_rows:
            product_row_total += len(extra_rows)
            issues.append(f"商品明细多识别 {len(extra_rows)} 行")

        date_total += 1
        date_check = result.get("date_check") or result.get("date") or {}
        actual_date = date_check.get("actual", "")
        expected_date_present = bool(truth.get("date_present", True))
        if expected_date_present:
            date_present_total += 1
            if actual_date:
                date_present_detected += 1
                date_detected += 1
        else:
            date_absent_total += 1
            if not actual_date:
                date_absent_correct += 1
        if actual_date == truth.get("actual_date"):
            date_correct += 1
        else:
            issues.append("收货日期未正确识别")

        required_date = str((truth.get("fields") or {}).get("要求到货", ""))
        expected_date_status = (
            "匹配" if truth.get("actual_date") == required_date else "不匹配"
        ) if expected_date_present else "未识别"
        date_status = date_check.get("status", "")
        if (
            expected_date_present
            and date_check.get("reliable") is True
            and date_status in {"匹配", "不匹配"}
        ):
            date_decision_total += 1
            if date_status == expected_date_status:
                date_decision_correct += 1

        seal_total += 1
        seal_check = result.get("seal_check") or result.get("seal") or {}
        expected_seal_status = "匹配" if truth.get("seal_should_match") else "不匹配"
        if seal_check.get("status") == expected_seal_status:
            seal_correct += 1
        else:
            issues.append("印章核验结论错误")
        if seal_check.get("reliable") is True and seal_check.get("status") in {"匹配", "不匹配"}:
            seal_decision_total += 1
            if seal_check.get("status") == expected_seal_status:
                seal_decision_correct += 1
        seal_scores.append(float(seal_check.get("score", 0)))
        samples.append({
            "filename": filename,
            "status": result.get("overall", ""),
            "review_status": result.get("review_status", ""),
            "issues": issues or result.get("review_reasons", []),
        })

    by_field = [
        {
            "field": name,
            "correct": count["correct"],
            "total": count["total"],
            "accuracy": _ratio(count["correct"], count["total"]),
        }
        for name, count in sorted(field_stats.items())
    ]
    product_by_column = [
        {
            "column": name,
            "correct": product_stats.get(name, Counter())["correct"],
            "total": product_stats.get(name, Counter())["total"],
            "accuracy": _ratio(
                product_stats.get(name, Counter())["correct"],
                product_stats.get(name, Counter())["total"],
            ),
        }
        for name in PRODUCT_COLUMNS if product_stats.get(name, Counter())["total"]
    ]
    labeled_fields = {name for truth in ground_truth.values() for name in truth.get("fields", {})}
    separately_evaluated = ["签收日期"] if any("actual_date" in truth for truth in ground_truth.values()) else []
    field_coverage = {
        "output_fields": list(OUTPUT_FIELDS),
        "evaluated_fields": [name for name in OUTPUT_FIELDS if field_stats.get(name, {}).get("total", 0)],
        "separately_evaluated_fields": separately_evaluated,
        "unlabeled_fields": [name for name in OUTPUT_FIELDS if name not in labeled_fields and name not in separately_evaluated],
        "by_field": [{
            "field": name,
            "labeled_samples": sum(name in truth.get("fields", {}) for truth in ground_truth.values()),
            "evaluated_samples": field_stats.get(name, {}).get("total", 0),
        } for name in OUTPUT_FIELDS],
    }
    return {
        "tested_samples": len(latest),
        "ground_truth_samples": len(ground_truth),
        "field_accuracy": _ratio(field_correct, field_total),
        "field_correct": field_correct,
        "field_total": field_total,
        "field_by_name": by_field,
        "field_coverage": field_coverage,
        "product_cell_accuracy": _ratio(product_cell_correct, product_cell_total),
        "product_cell_correct": product_cell_correct,
        "product_cell_total": product_cell_total,
        "product_row_accuracy": _ratio(product_row_correct, product_row_total),
        "product_row_correct": product_row_correct,
        "product_row_total": product_row_total,
        "product_by_column": product_by_column,
        "date_accuracy": _ratio(date_correct, date_total),
        "date_detection_rate": _ratio(date_present_detected, date_present_total),
        "date_present_total": date_present_total,
        "date_present_detected": date_present_detected,
        "date_absent_total": date_absent_total,
        "date_absent_correct": date_absent_correct,
        "date_absent_accuracy": _ratio(date_absent_correct, date_absent_total),
        "date_decision_coverage": _ratio(date_decision_total, date_total),
        "date_decision_accuracy": _ratio(date_decision_correct, date_decision_total),
        "date_decision_correct": date_decision_correct,
        "date_decision_total": date_decision_total,
        "date_correct": date_correct,
        "date_total": date_total,
        "seal_conclusion_accuracy": _ratio(seal_correct, seal_total),
        "seal_decision_coverage": _ratio(seal_decision_total, seal_total),
        "seal_decision_accuracy": _ratio(seal_decision_correct, seal_decision_total),
        "seal_decision_correct": seal_decision_correct,
        "seal_decision_total": seal_decision_total,
        "seal_correct": seal_correct,
        "seal_total": seal_total,
        "seal_average_similarity": round(sum(seal_scores) / len(seal_scores), 3) if seal_scores else 0.0,
        "samples": samples,
    }


def evaluate_backends(results: list[dict], ground_truth: dict) -> list[dict]:
    """Compare each logical backend on its own latest complete result per sample."""
    groups: dict[str, list[dict]] = {}
    for result in results:
        backend = str(result.get("ocr_backend", "")).strip()
        if backend:
            groups.setdefault(backend, []).append(result)
    order = {name: index for index, name in enumerate(
        ("vision", "paddle", "paddle_server", "hybrid", "hybrid_server")
    )}
    output = []
    for backend, group in groups.items():
        metrics = evaluate_results(group, ground_truth)
        latest_by_filename = {}
        for item in sorted(group, key=lambda row: row.get("id", 0)):
            if item.get("filename") in ground_truth:
                latest_by_filename[item["filename"]] = item
        seconds = [
            float(item.get("processing_seconds", 0))
            for item in latest_by_filename.values() if item.get("processing_seconds") is not None
        ]
        output.append({
            "backend": backend,
            "label": backend_label(backend),
            "samples": metrics["tested_samples"],
            "field_accuracy": metrics["field_accuracy"],
            "field_total": metrics["field_total"],
            "product_cell_accuracy": metrics["product_cell_accuracy"],
            "product_cell_total": metrics["product_cell_total"],
            "date_accuracy": metrics["date_accuracy"],
            "date_total": metrics["date_total"],
            "date_decision_coverage": metrics["date_decision_coverage"],
            "date_decision_accuracy": metrics["date_decision_accuracy"],
            "seal_conclusion_accuracy": metrics["seal_conclusion_accuracy"],
            "seal_total": metrics["seal_total"],
            "seal_decision_coverage": metrics["seal_decision_coverage"],
            "seal_decision_accuracy": metrics["seal_decision_accuracy"],
            "average_seconds": round(sum(seconds) / len(seconds), 2) if seconds else 0.0,
            "timed_samples": len(seconds),
        })
    return sorted(output, key=lambda item: (order.get(item["backend"], 99), item["backend"]))


def operational_report(results: list[dict], review_history: list[dict], accuracy: dict) -> dict:
    review_counts = Counter(item.get("review_status", "待复核") for item in results)
    overall_counts = Counter(item.get("final_result") or item.get("overall", "") for item in results)
    error_counts = Counter(
        item.get("error_type") or reason
        for item in results
        for reason in (item.get("review_reasons") or ([item.get("error_type")] if item.get("error_type") else []))
    )
    correction_types = Counter(row.get("error_type") or row.get("action", "人工修改") for row in review_history)
    return {
        "total_results": len(results),
        "review_counts": dict(review_counts),
        "overall_counts": dict(overall_counts),
        "error_types": [{"type": key, "count": value} for key, value in error_counts.most_common()],
        "correction_types": [{"type": key, "count": value} for key, value in correction_types.most_common()],
        "accuracy": accuracy,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
