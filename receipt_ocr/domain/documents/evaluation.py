from __future__ import annotations

from .pagination import merge_paginated_results


def evaluate_document_routing(truth: dict, results: list[dict]) -> dict:
    """Evaluate non-standard page routing without polluting receipt metrics."""
    by_filename = {str(item.get("filename") or ""): item for item in results}
    samples = []
    type_correct = 0
    row_count_correct = 0
    row_count_total = 0
    for filename, expected in (truth.get("documents") or {}).items():
        item = by_filename.get(filename)
        actual_type = str(((item or {}).get("document_type") or {}).get("type") or "")
        expected_type = str(expected.get("type") or "")
        type_ok = bool(item) and actual_type == expected_type
        type_correct += int(type_ok)
        expected_rows = expected.get("product_row_count")
        actual_rows = len((((item or {}).get("product_table") or {}).get("rows") or []))
        row_ok = None
        if expected_rows is not None:
            row_count_total += 1
            row_ok = actual_rows == int(expected_rows)
            row_count_correct += int(row_ok)
        samples.append({
            "filename": filename,
            "found": item is not None,
            "expected_type": expected_type,
            "actual_type": actual_type,
            "type_correct": type_ok,
            "expected_product_rows": expected_rows,
            "actual_product_rows": actual_rows if item else None,
            "product_row_count_correct": row_ok,
        })

    merged = merge_paginated_results(results)
    logical_samples = []
    logical_correct = 0
    for key, expected in (truth.get("logical_receipts") or {}).items():
        item = next(
            (
                candidate for candidate in merged
                if str((candidate.get("page_group") or {}).get("key") or "") == key
            ),
            None,
        )
        page_group = (item or {}).get("page_group") or {}
        rows = (((item or {}).get("product_table") or {}).get("rows") or [])
        row_numbers = []
        for row in rows:
            value = str((row.get("values") or {}).get("行号") or "").strip()
            row_numbers.append(int(value) if value.isdigit() else None)
        first = int(expected.get("first_row_number") or 0)
        last = int(expected.get("last_row_number") or 0)
        step = int(expected.get("row_number_step") or 1)
        expected_numbers = list(range(first, last + step, step)) if first and last else []
        checks = {
            "page_count": int(page_group.get("page_count") or 0) == int(expected["page_count"]),
            "product_row_count": len(rows) == int(expected["product_row_count"]),
            "numeric_row_order": row_numbers == expected_numbers,
        }
        sample_ok = bool(item) and all(checks.values())
        logical_correct += int(sample_ok)
        logical_samples.append({
            "key": key,
            "found": item is not None,
            "checks": checks,
            "actual_page_count": int(page_group.get("page_count") or 0),
            "actual_product_rows": len(rows),
            "actual_first_row_number": row_numbers[0] if row_numbers else None,
            "actual_last_row_number": row_numbers[-1] if row_numbers else None,
            "correct": sample_ok,
        })

    document_total = len(samples)
    logical_total = len(logical_samples)
    return {
        "documents": {
            "total": document_total,
            "found": sum(int(item["found"]) for item in samples),
            "type_correct": type_correct,
            "type_accuracy": round(type_correct / document_total, 4) if document_total else 0.0,
            "product_row_count_correct": row_count_correct,
            "product_row_count_total": row_count_total,
            "product_row_count_accuracy": (
                round(row_count_correct / row_count_total, 4) if row_count_total else 0.0
            ),
            "samples": samples,
        },
        "logical_receipts": {
            "total": logical_total,
            "correct": logical_correct,
            "accuracy": round(logical_correct / logical_total, 4) if logical_total else 0.0,
            "samples": logical_samples,
        },
    }
