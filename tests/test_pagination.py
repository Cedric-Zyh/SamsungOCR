from pathlib import Path

from receipt_ocr.database import Database
from receipt_ocr.pagination import merge_paginated_results, page_identity


def result(filename: str, kind: str, row_number: str, *, task_id: str = "task") -> dict:
    return {
        "filename": filename,
        "task_id": task_id,
        "overall": "需人工复核" if kind == "product_continuation" else "通过",
        "final_result": "需人工复核" if kind == "product_continuation" else "通过",
        "review_status": "待复核" if kind == "product_continuation" else "无需复核",
        "document_type": {"type": kind, "label": kind},
        "fields": {"客户订单号": "7284571207"} if kind == "receipt" else {},
        "product_table": {
            "columns": ["行号"],
            "rows": [{"index": 0, "values": {"行号": row_number}}],
            "confidence": .98,
            "source": "OCR 按列定位",
        },
        "date_check": {"status": "匹配", "actual": "2025-04-24"},
        "seal_check": {"status": "匹配", "score": .9},
    }


def test_page_identity_only_recognizes_two_digit_scanner_suffix():
    assert page_identity("7284571207.jpg") == ("7284571207", 0)
    assert page_identity("7284571207_01.jpg") == ("7284571207", 1)
    assert page_identity("customer_a.jpg") == ("customer_a", 0)


def test_export_projection_merges_continuation_rows_into_one_receipt():
    cover = {**result("7284571207.jpg", "receipt", "10"), "id": 1}
    continuation = {**result("7284571207_01.jpg", "product_continuation", "40"), "id": 2}
    cover["fields"].update({"要求到货": "2025-04-25", "签章要求": ""})
    cover["date_check"] = {"status": "未识别", "actual": "", "confidence": 0}
    continuation["fields"] = {
        "签章要求": "北京罗凡尼科技发展有限公司",
        "签收说明": "如未签实收数量视为整单完整签收",
    }
    continuation["date_check"] = {
        "status": "无法判断", "actual": "2025-04-24", "confidence": .91,
    }
    continuation["seal_check"] = {
        "status": "匹配", "recognized": "北京罗凡尼科技发展有限公司",
        "all_recognized": ["北京罗凡尼科技发展有限公司"], "score": .95,
        "confidence": .95, "reliable": True,
    }
    continuation["processing_artifacts"] = {
        "date": [{"original_url": "/date.jpg"}],
        "seals": [{"original_url": "/seal.jpg"}],
    }

    merged = merge_paginated_results([continuation, cover])

    assert len(merged) == 1
    assert merged[0]["filename"] == "7284571207.jpg"
    assert merged[0]["page_group"]["page_count"] == 2
    assert [row["values"]["行号"] for row in merged[0]["product_table"]["rows"]] == ["10", "40"]
    assert merged[0]["review_status"] == "待复核"
    assert merged[0]["document_type"]["label"] == "三星出库回单（2页）"
    assert merged[0]["fields"]["签章要求"] == "北京罗凡尼科技发展有限公司"
    assert merged[0]["date_check"]["status"] == "不匹配"
    assert merged[0]["date_check"]["actual"] == "2025-04-24"
    assert merged[0]["date_check"]["source_page"] == "7284571207_01.jpg"
    assert merged[0]["seal_check"]["status"] == "匹配"
    assert merged[0]["processing_artifacts"]["date"][0]["original_url"] == "/date.jpg"


def test_paginated_rows_use_numeric_order_instead_of_lexicographic_order():
    cover = {**result("7284571207.jpg", "receipt", "10"), "id": 1}
    cover["product_table"]["rows"] = [
        {"index": 0, "values": {"行号": "10"}},
        {"index": 1, "values": {"行号": "100"}},
        {"index": 2, "values": {"行号": "20"}},
    ]
    continuation = {**result("7284571207_01.jpg", "product_continuation", "40"), "id": 2}
    continuation["product_table"]["rows"] = [
        {"index": 0, "values": {"行号": "40"}},
        {"index": 1, "values": {"行号": "110"}},
        {"index": 2, "values": {"行号": "30"}},
    ]

    merged = merge_paginated_results([continuation, cover])[0]

    assert [row["values"]["行号"] for row in merged["product_table"]["rows"]] == [
        "10", "20", "30", "40", "100", "110",
    ]
    assert [row["index"] for row in merged["product_table"]["rows"]] == list(range(6))
    assert merged["product_table"]["row_order"] == "numeric_row_number"
    assert "唯一数字行号排序" in merged["product_table"]["source"]


def test_paginated_rows_keep_ocr_order_when_row_numbers_are_not_unique_numeric_values():
    cover = {**result("7284571207.jpg", "receipt", "10"), "id": 1}
    cover["product_table"]["rows"] = [
        {"index": 0, "values": {"行号": "10"}},
        {"index": 1, "values": {"行号": "待复核"}},
    ]
    continuation = {**result("7284571207_01.jpg", "product_continuation", "20"), "id": 2}

    merged = merge_paginated_results([cover, continuation])[0]

    assert [row["values"]["行号"] for row in merged["product_table"]["rows"]] == [
        "10", "待复核", "20",
    ]
    assert merged["product_table"]["row_order"] == "ocr_physical_order"


def test_paginated_human_override_replaces_projection_without_duplicating_rows():
    cover = {**result("7284571207.jpg", "receipt", "10"), "id": 1}
    continuation = {**result("7284571207_01.jpg", "product_continuation", "40"), "id": 2}
    cover["page_review_override"] = {
        "fields": {"客户订单号": "7284571207", "签章要求": "人工确认章"},
        "product_table": {
            "columns": ["行号"],
            "rows": [
                {"index": 0, "values": {"行号": "10"}},
                {"index": 1, "values": {"行号": "40"}},
            ],
            "confidence": 1.0,
            "source": "人工复核",
        },
        "date_check": {"status": "不匹配", "actual": "2025-04-24", "reliable": True},
        "seal_check": {"status": "匹配", "recognized": "人工确认章", "reliable": True},
        "review_status": "确认不通过",
        "final_result": "不通过",
        "overall": "不通过",
        "review_reasons": [],
        "human_note": "两页人工确认",
    }

    merged = merge_paginated_results([cover, continuation])[0]

    assert [row["values"]["行号"] for row in merged["product_table"]["rows"]] == ["10", "40"]
    assert merged["date_check"]["actual"] == "2025-04-24"
    assert merged["review_status"] == "确认不通过"
    assert merged["page_review_applied"] is True


def test_orphan_continuation_is_not_hidden():
    continuation = {**result("7284571207_01.jpg", "product_continuation", "40"), "id": 2}
    assert merge_paginated_results([continuation]) == [continuation]


def test_database_persists_page_relationship_after_second_page(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("task", "two pages", 2, "hybrid")
    cover_id = database.insert_result(
        filename="7284571207.jpg", stored_name="sample:cover", preview_name="cover.jpg",
        task_id="task", result=result("7284571207.jpg", "receipt", "10"),
    )
    continuation_id = database.insert_result(
        filename="7284571207_01.jpg", stored_name="sample:page2", preview_name="page2.jpg",
        task_id="task", result=result("7284571207_01.jpg", "product_continuation", "40"),
    )

    cover = database.get_result(cover_id)
    continuation = database.get_result(continuation_id)
    assert cover["page_role"] == "cover"
    assert cover["page_group"]["page_count"] == 2
    assert continuation["page_role"] == "continuation"
    assert continuation["parent_result_id"] == cover_id
    assert continuation["page_index"] == 1
