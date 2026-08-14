from __future__ import annotations

import copy
import re
from collections import defaultdict

from .parser import compare_dates, parse_date


PAGE_SUFFIX = re.compile(r"^(?P<base>.+?)_(?P<page>\d{2})(?P<ext>\.[^.]+)$")


def page_identity(filename: str) -> tuple[str, int]:
    """Return the receipt group key and scanner page number."""
    name = str(filename or "")
    match = PAGE_SUFFIX.match(name)
    if not match:
        return name.rsplit(".", 1)[0], 0
    return match.group("base"), int(match.group("page"))


def page_group_candidates(results: list[dict]) -> list[dict]:
    """Describe cover/continuation relationships inside one batch task."""
    groups: dict[tuple[str, str], list[tuple[int, dict]]] = defaultdict(list)
    for item in results:
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue
        key, page_index = page_identity(str(item.get("filename") or ""))
        groups[(task_id, key)].append((page_index, item))

    output = []
    for (task_id, key), members in groups.items():
        covers = [
            item for index, item in members
            if index == 0 and item.get("document_type", {}).get("type") == "receipt"
        ]
        continuations = sorted(
            (
                (index, item) for index, item in members
                if index > 0
                and item.get("document_type", {}).get("type") == "product_continuation"
            ),
            key=lambda pair: pair[0],
        )
        if not covers or not continuations:
            continue
        cover = max(covers, key=lambda item: int(item.get("id") or 0))
        output.append({
            "task_id": task_id,
            "key": key,
            "cover": cover,
            "continuations": continuations,
        })
    return output


def _sort_rows_by_unique_numeric_row_number(rows: list[dict]) -> tuple[list[dict], bool]:
    """Restore business order when every merged product row has a unique number.

    Paddle can return long tables in lexicographic order (10, 100, ..., 20).
    Sorting is safe only when every row number is present, numeric and unique;
    otherwise the physical OCR order is kept for human review.
    """
    numbered: list[tuple[int, dict]] = []
    seen: set[int] = set()
    for row in rows:
        value = str((row.get("values") or {}).get("行号") or "").strip()
        if not re.fullmatch(r"\d+", value):
            return rows, False
        number = int(value)
        if number in seen:
            return rows, False
        seen.add(number)
        numbered.append((number, row))
    if numbered == sorted(numbered, key=lambda pair: pair[0]):
        return rows, False
    return [row for _, row in sorted(numbered, key=lambda pair: pair[0])], True


def merge_paginated_results(results: list[dict]) -> list[dict]:
    """Project stored page records to one export result per receipt."""
    groups = page_group_candidates(results)
    consumed: set[int] = set()
    merged_by_cover: dict[int, dict] = {}
    for group in groups:
        cover = group["cover"]
        cover_id = int(cover.get("id") or 0)
        merged = copy.deepcopy(cover)
        tables = [merged.get("product_table") or {}]
        pages = [cover]
        for _, continuation in group["continuations"]:
            pages.append(continuation)
            tables.append(continuation.get("product_table") or {})
            consumed.add(int(continuation.get("id") or 0))

        rows = []
        columns = []
        confidences = []
        sources = []
        for table in tables:
            if table.get("columns") and not columns:
                columns = list(table["columns"])
            sources.append(str(table.get("source") or ""))
            if table.get("rows"):
                confidences.append(float(table.get("confidence") or 0))
            for row in table.get("rows") or []:
                copied = copy.deepcopy(row)
                copied["index"] = len(rows)
                rows.append(copied)
        rows, rows_sorted = _sort_rows_by_unique_numeric_row_number(rows)
        for index, row in enumerate(rows):
            row["index"] = index
        merged["product_table"] = {
            "columns": columns,
            "rows": rows,
            "confidence": round(min(confidences), 3) if confidences else 0.0,
            "source": (
                " + ".join(filter(None, sources))
                + " + 分页关联合并"
                + (" + 唯一数字行号排序" if rows_sorted else "")
            ),
            "row_order": "numeric_row_number" if rows_sorted else "ocr_physical_order",
        }
        merged["page_group"] = {
            "key": group["key"],
            "page_count": len(pages),
            "cover_result_id": cover_id,
            "continuation_result_ids": [int(item.get("id") or 0) for item in pages[1:]],
            "filenames": [str(item.get("filename") or "") for item in pages],
        }
        merged["source_filenames"] = merged["page_group"]["filenames"]
        merged["document_type"] = {
            **(merged.get("document_type") or {}),
            "label": f"三星出库回单（{len(pages)}页）",
        }

        # A scanner may put the business fields and the signature footer on
        # different pages.  Keep page records independent in the database,
        # then project the footer evidence back onto the cover for review and
        # export.  Never infer a date or stamp from the filename/page order.
        footer = next(
            (
                item for item in reversed(pages[1:])
                if str((item.get("fields") or {}).get("签章要求") or "").strip()
            ),
            None,
        )
        if footer is not None:
            footer_fields = footer.get("fields") or {}
            merged_fields = merged.setdefault("fields", {})
            for field_name in ("签章要求", "签收说明"):
                value = str(footer_fields.get(field_name) or "").strip()
                if value:
                    merged_fields[field_name] = value
                    metadata = (footer.get("field_metadata") or {}).get(field_name)
                    if metadata:
                        merged.setdefault("field_metadata", {})[field_name] = copy.deepcopy(metadata)

            footer_date = footer.get("date_check") or {}
            actual = parse_date(str(footer_date.get("actual") or ""))
            if actual is not None:
                date_check = compare_dates(str(merged_fields.get("要求到货") or ""), actual)
                confidence = float(footer_date.get("confidence") or 0)
                date_check.update(
                    confidence=confidence,
                    reliable=bool(confidence >= 0.72),
                    source_page=str(footer.get("filename") or ""),
                )
                merged["date_check"] = date_check

            footer_seal = footer.get("seal_check") or {}
            if footer_seal.get("recognized") or footer_seal.get("all_recognized"):
                merged["seal_check"] = copy.deepcopy(footer_seal)
                merged["seal_check"]["source_page"] = str(footer.get("filename") or "")

            merged["date_ocr_texts"] = list(footer.get("date_ocr_texts") or [])
            merged["seal_regions"] = copy.deepcopy(footer.get("seal_regions") or [])
            merged["processing_artifacts"] = copy.deepcopy(
                footer.get("processing_artifacts") or {"date": [], "seals": []}
            )
            merged["page_group"]["footer_result_id"] = int(footer.get("id") or 0)

        reasons = [
            reason for reason in (merged.get("review_reasons") or [])
            if "首页未包含签收页脚" not in reason and "等待商品续页关联" not in reason
        ]
        merged["review_reasons"] = reasons

        # A human reviews the logical receipt, not each scanner page.  Keep
        # the audited merged values as an override on the cover record so the
        # physical page OCR remains immutable and a later projection does not
        # append the continuation rows twice.
        override = cover.get("page_review_override") or {}
        if override:
            for key in (
                "fields", "field_metadata", "product_table", "date_check",
                "seal_check", "review_reasons", "review_status",
                "final_result", "overall", "human_note", "error_type",
            ):
                if key in override:
                    merged[key] = copy.deepcopy(override[key])
            merged["page_review_applied"] = True
        else:
            statuses = {str(item.get("review_status") or "") for item in pages}
            final_results = {str(item.get("final_result") or item.get("overall") or "") for item in pages}
            if "确认不通过" in statuses or "不通过" in final_results:
                merged.update(review_status="确认不通过", final_result="不通过", overall="不通过")
            elif "待复核" in statuses:
                merged.update(review_status="待复核", final_result="需人工复核", overall="需人工复核")
                reasons = list(merged.get("review_reasons") or [])
                if "商品明细续页待人工复核" not in reasons:
                    reasons.append("商品明细续页待人工复核")
                merged["review_reasons"] = reasons
        note = str(merged.get("human_note") or "").strip()
        page_note = "分页合并：" + "、".join(merged["source_filenames"])
        merged["human_note"] = note if page_note in note else "；".join(filter(None, (note, page_note)))
        merged_by_cover[cover_id] = merged

    output = []
    for item in results:
        item_id = int(item.get("id") or 0)
        if item_id in consumed:
            continue
        output.append(merged_by_cover.get(item_id, item))
    return output
