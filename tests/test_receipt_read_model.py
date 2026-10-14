"""Bound full-evidence reads to selected logical receipts without losing pages."""
from copy import deepcopy
import json
import pytest
from receipt_ocr.persistence.database import Database
from receipt_ocr.domain.documents.pagination import merge_paginated_results


def add(db, name, kind, *, customer="客户", task="task"):
    payload = {"filename": name, "overall": "通过", "review_status": "无需复核",
               "document_type": {"type": kind}, "fields": {"客户名称": customer},
               "date_check": {"status": "匹配", "reliable": True},
               "seal_check": {"status": "匹配", "reliable": True},
               "product_table": {"rows": [{"values": {"行号": "1" if kind == "receipt" else "2"}}]},
               "ocr_observations": [{"text": name * 1000}],
               "processing_artifacts": {"date": [{"original_url": "/" + name}]}}
    return db.insert_result(filename=name, stored_name=name, preview_name="", task_id=task, result=payload)


def test_pagination_hydrates_only_selected_cover_and_its_continuations(tmp_path, monkeypatch):
    db = Database(tmp_path / "results.db")
    db.initialize()
    for index in range(12):
        add(db, f"{index}.jpg", "receipt")
        add(db, f"{index}_01.jpg", "product_continuation")
    expected = merge_paginated_results(db.list_results(limit=100))[3:5]
    decoded = []
    original = db._row_to_result
    def decode(row):
        if "ocr_observations" in json.loads(row["result_json"]):
            decoded.append(row["id"])
        return original(row)
    monkeypatch.setattr(db, "_row_to_result", decode)
    page = db.query_receipts(offset=3, limit=2)
    assert page["total"] == 12
    assert page["items"] == expected
    assert len(decoded) == 4
    assert all(item["page_group"]["page_count"] == 2 for item in page["items"])


def test_summary_and_review_write_share_transaction_and_rollback(tmp_path):
    db = Database(tmp_path / "results.db")
    db.initialize()
    identity = add(db, "a.jpg", "receipt", customer="旧客户", task="")
    original = db.get_result(identity)
    with pytest.raises(RuntimeError):
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            edited = deepcopy(original)
            edited["fields"]["客户名称"] = "新客户"
            db.review_result(identity, result=edited, review_status="确认通过", final_result="通过",
                             note="", action="确认通过", _connection=connection)
            assert db.query_receipts(filters={"customer": "新客户"}, _connection=connection)["total"] == 1
            assert db.get_result(identity, _connection=connection)["fields"]["客户名称"] == "新客户"
            raise RuntimeError("abort review")
    assert db.query_receipts(filters={"customer": "新客户"})["total"] == 0
    assert db.query_receipts(filters={"customer": "旧客户"})["total"] == 1
    assert not db.history(identity)
    edited = deepcopy(original)
    edited["fields"]["客户名称"] = "重识别客户"
    db.replace_after_retry(identity, edited, "retry.jpg")
    assert db.query_receipts(filters={"customer": "重识别客户"})["items"][0]["preview_name"] == "retry.jpg"


def test_status_counts_keep_scope_and_latest_decisions_across_pages(tmp_path, monkeypatch):
    from receipt_ocr.web import application as web
    db = Database(tmp_path / "counts.db")
    db.initialize()
    for filename, customer, status in (
        ("a.jpg", "甲", "通过"), ("b.jpg", "甲", "通过"),
        ("a.jpg", "甲", "不通过"), ("c.jpg", "甲", "需人工复核"),
        ("d.jpg", "甲", "通过"), ("e.jpg", "乙", "通过"),
    ):
        db.insert_result(filename=filename, stored_name=filename, preview_name="", task_id="",
                         result={"overall": "通过", "final_result": status,
                                 "fields": {"客户名称": customer}})
    day = db.get_result(1)["created_at"][:10]
    filters = {"import_date": day, "customer": "甲", "overall": "通过"}
    expected = {"": 4, "通过": 2, "需人工复核": 1, "不通过": 1, "识别失败": 0}
    for offset in (0, 1):
        result = db.query_receipts(filters=filters, latest_by_filename=True,
                                   limit=1, offset=offset, include_status_counts=True)
        assert result["total"] == 2
        assert len(result["items"]) == 1
        assert result["status_counts"] == expected
    assert "status_counts" not in db.query_receipts(filters=filters)
    assert db.query_receipts(filters={**filters, "customer": "不存在"},
                             include_status_counts=True)["status_counts"][""] == 0
    monkeypatch.setattr(web, "database", db)
    response = web.app.test_client().get("/api/results", query_string={
        **filters, "page": 1, "page_size": 1, "include_status_counts": "1"})
    assert response.status_code == 200
    assert response.json["status_counts"] == expected
