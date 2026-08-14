from receipt_ocr.document_evaluation import evaluate_document_routing


def page(filename: str, kind: str, rows: list[str], *, item_id: int) -> dict:
    return {
        "id": item_id,
        "filename": filename,
        "task_id": "task",
        "document_type": {"type": kind, "label": kind},
        "product_table": {
            "columns": ["行号"],
            "rows": [
                {"index": index, "values": {"行号": number}}
                for index, number in enumerate(rows)
            ],
        },
        "fields": {},
        "date_check": {},
        "seal_check": {},
    }


def test_document_routing_truth_keeps_non_receipts_out_of_receipt_metrics():
    truth = {
        "documents": {
            "auth.jpg": {"type": "warehouse_authorization"},
            "7284571207.jpg": {"type": "receipt", "product_row_count": 2},
            "7284571207_01.jpg": {"type": "product_continuation", "product_row_count": 2},
        },
        "logical_receipts": {
            "7284571207": {
                "page_count": 2,
                "product_row_count": 4,
                "first_row_number": 10,
                "last_row_number": 40,
                "row_number_step": 10,
            }
        },
    }
    results = [
        page("auth.jpg", "warehouse_authorization", [], item_id=1),
        page("7284571207.jpg", "receipt", ["10", "30"], item_id=2),
        page("7284571207_01.jpg", "product_continuation", ["20", "40"], item_id=3),
    ]

    report = evaluate_document_routing(truth, results)

    assert report["documents"]["type_accuracy"] == 1.0
    assert report["documents"]["product_row_count_accuracy"] == 1.0
    assert report["logical_receipts"]["accuracy"] == 1.0


def test_document_routing_report_exposes_missing_and_misclassified_pages():
    truth = {
        "documents": {
            "missing.jpg": {"type": "warehouse_authorization"},
            "wrong.jpg": {"type": "warehouse_authorization"},
        }
    }
    results = [page("wrong.jpg", "receipt", [], item_id=1)]

    report = evaluate_document_routing(truth, results)

    assert report["documents"]["found"] == 1
    assert report["documents"]["type_correct"] == 0
    assert report["documents"]["samples"][0]["found"] is False
