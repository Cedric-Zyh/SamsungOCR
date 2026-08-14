from __future__ import annotations

import copy
from statistics import mean

from .document_evaluation import evaluate_document_routing
from .evaluation import evaluate_results
from .parser import PRODUCT_COLUMNS, normalize_text


def compare_backend_runs(
    runs: dict[str, list[dict]],
    ground_truth: dict,
    document_truth: dict,
    *,
    reference_backend: str,
) -> dict:
    """Compare offline backend runs without writing benchmark noise to SQLite."""
    prepared = {
        backend: _prepare_results(results, backend)
        for backend, results in runs.items()
    }
    reference = prepared.get(reference_backend)
    if reference is None:
        raise ValueError(f"缺少参考后端：{reference_backend}")

    output = []
    for backend, results in prepared.items():
        filenames = {str(item.get("filename") or "") for item in results}
        receipt_truth = {
            filename: value for filename, value in ground_truth.items()
            if filename in filenames
        }
        routing_truth = _filter_document_truth(document_truth, filenames)
        receipt_metrics = evaluate_results(results, receipt_truth)
        routing_metrics = evaluate_document_routing(routing_truth, results)
        seconds = [
            float(item.get("processing_seconds") or 0)
            for item in results if item.get("processing_seconds") is not None
        ]
        output.append({
            "backend": backend,
            "files": len(results),
            "failures": sum(int(bool(item.get("error"))) for item in results),
            "average_seconds": round(mean(seconds), 2) if seconds else 0.0,
            "receipt_metrics": {
                key: receipt_metrics[key]
                for key in (
                    "tested_samples", "field_accuracy", "field_correct", "field_total",
                    "product_cell_accuracy", "product_cell_correct", "product_cell_total",
                    "product_row_accuracy", "product_row_correct", "product_row_total",
                    "date_accuracy", "date_correct", "date_total",
                    "date_decision_accuracy", "date_decision_coverage",
                    "seal_conclusion_accuracy", "seal_correct", "seal_total",
                    "seal_decision_accuracy", "seal_decision_coverage",
                )
            },
            "routing_metrics": {
                "documents": {
                    key: routing_metrics["documents"][key]
                    for key in (
                        "total", "found", "type_correct", "type_accuracy",
                        "product_row_count_correct", "product_row_count_total",
                        "product_row_count_accuracy",
                    )
                },
                "logical_receipts": {
                    key: routing_metrics["logical_receipts"][key]
                    for key in ("total", "correct", "accuracy")
                },
            },
            "product_agreement_with_reference": _product_agreement(reference, results),
        })
    return {
        "reference_backend": reference_backend,
        "backends": output,
        "notes": [
            "product_agreement_with_reference 是与已核验参考后端的逐单元格一致率，不冒充人工真值准确率",
            "文档路由真值与标准回单字段/日期/印章真值分开统计",
        ],
    }


def _prepare_results(results: list[dict], backend: str) -> list[dict]:
    # ``--run backend=file`` may be repeated to combine an initial batch and a
    # targeted rerun.  The later file is authoritative for the same filename;
    # counting both would inflate file/time totals and can make two physical
    # pages look like a broken multi-page logical receipt.
    latest_by_filename: dict[str, dict] = {}
    for index, item in enumerate(results, start=1):
        copied = copy.deepcopy(item)
        copied.setdefault("id", index)
        copied["task_id"] = f"offline-benchmark:{backend}"
        filename = str(copied.get("filename") or "").strip()
        key = filename or f"__missing_filename_{index}"
        latest_by_filename[key] = copied
    output = list(latest_by_filename.values())
    for index, item in enumerate(output, start=1):
        item["id"] = index
    return output


def _filter_document_truth(document_truth: dict, filenames: set[str]) -> dict:
    documents = {
        filename: value
        for filename, value in (document_truth.get("documents") or {}).items()
        if filename in filenames
    }
    logical = {}
    for key, value in (document_truth.get("logical_receipts") or {}).items():
        page_names = [name for name in documents if name.rsplit(".", 1)[0].split("_", 1)[0] == key]
        if len(page_names) == int(value.get("page_count") or 0):
            logical[key] = value
    return {"documents": documents, "logical_receipts": logical}


def _product_agreement(reference: list[dict], candidate: list[dict]) -> dict:
    expected = _product_cells(reference)
    actual = _product_cells(candidate)
    total = len(expected)
    matching = sum(
        int(normalize_text(actual.get(key, "")) == normalize_text(value))
        for key, value in expected.items()
    )
    return {
        "matching": matching,
        "total": total,
        "rate": round(matching / total, 4) if total else 0.0,
    }


def _product_cells(results: list[dict]) -> dict[tuple[str, str, str], str]:
    output = {}
    for item in results:
        filename = str(item.get("filename") or "")
        for row in ((item.get("product_table") or {}).get("rows") or []):
            values = row.get("values") or {}
            number = str(values.get("行号") or "").strip()
            if not number:
                continue
            for column in PRODUCT_COLUMNS:
                if column in values:
                    output[(filename, number, column)] = str(values.get(column) or "")
    return output
