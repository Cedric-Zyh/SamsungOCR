import json

import app as app_module

from app import _apply_human_edits
from receipt_ocr.database import Database
from receipt_ocr.evaluation import GROUND_TRUTH_FIELDS
from receipt_ocr.parser import PRODUCT_COLUMNS


def sample_result(filename="7266301052.jpg"):
    return {
        "filename": filename,
        "overall": "需人工复核",
        "final_result": "需人工复核",
        "review_status": "待复核",
        "fields": {"客户订单号": "007266301052", "客户名称": "测试客户"},
        "date_check": {"actual": "", "status": "未识别"},
        "seal_check": {"recognized": "", "status": "未识别"},
    }


def test_human_edits_recompute_date_and_seal():
    current = {
        "fields": {"要求到货": "2025-01-05", "签章要求": "京小服科技服务有限公司维修中心专用章（04）"},
        "field_metadata": {},
        "date_check": {},
        "seal_check": {},
    }
    updated = _apply_human_edits(current, {
        "fields": current["fields"],
        "actual_date": "2025-01-05",
        "seal_text": "京小服科技服务有限公司维修中心专用章（04）",
    })
    assert updated["date_check"]["status"] == "匹配"
    assert updated["seal_check"]["status"] == "匹配"
    assert updated["overall"] == "通过"
    assert all(meta["source"] == "人工复核" for meta in updated["field_metadata"].values())


def test_confirm_pass_rejects_missing_date_and_seal_evidence(tmp_path, monkeypatch):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    monkeypatch.setattr(app_module, "database", test_database)
    result = sample_result("unsafe.jpg")
    result["field_metadata"] = {}
    result["fields"].update({"要求到货": "2025-01-05", "签章要求": "测试收货章"})
    result_id = test_database.insert_result(
        filename="unsafe.jpg", stored_name="sample:unsafe.jpg",
        preview_name="unsafe.jpg", task_id="", result=result,
    )

    response = app_module.app.test_client().patch(
        f"/api/results/{result_id}/review",
        json={
            "fields": result["fields"], "actual_date": "", "seal_text": "",
            "review_status": "确认通过", "final_result": "通过",
        },
    )

    assert response.status_code == 409
    assert "确认通过前请补全并核对" in response.get_json()["error"]
    assert test_database.get_result(result_id)["review_status"] == "待复核"
    assert test_database.history(result_id) == []


def test_results_show_latest_receipt_once_while_history_retains_prior_runs(
    tmp_path, monkeypatch
):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    monkeypatch.setattr(app_module, "database", test_database)
    stale = sample_result("same.jpg")
    stale["fields"]["客户名称"] = "旧客户"
    current = sample_result("same.jpg")
    current["fields"]["客户名称"] = "新客户"
    test_database.insert_result(
        filename="same.jpg", stored_name="sample:old", preview_name="old.jpg",
        task_id="", result=stale,
    )
    test_database.insert_result(
        filename="same.jpg", stored_name="sample:new", preview_name="new.jpg",
        task_id="", result=current,
    )
    client = app_module.app.test_client()

    results = client.get("/api/results").get_json()
    history = client.get("/api/history").get_json()

    assert len(results) == 1
    assert results[0]["fields"]["客户名称"] == "新客户"
    assert len(history) == 2
    assert client.get("/api/results?customer=旧客户").get_json() == []
    assert len(client.get("/api/history?customer=旧客户").get_json()) == 1


def test_bulk_pass_rejects_uncertain_record_but_accepts_reliable_pass(tmp_path, monkeypatch):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    monkeypatch.setattr(app_module, "database", test_database)

    unsafe = sample_result("unsafe.jpg")
    unsafe_id = test_database.insert_result(
        filename="unsafe.jpg", stored_name="sample:unsafe.jpg",
        preview_name="unsafe.jpg", task_id="", result=unsafe,
    )
    safe = sample_result("safe.jpg")
    safe.update(overall="通过", final_result="通过", review_status="无需复核")
    safe["date_check"] = {
        "actual": "2025-01-05", "status": "匹配", "reliable": True,
    }
    safe["seal_check"] = {
        "recognized": "测试收货章", "status": "匹配", "reliable": True,
    }
    safe_id = test_database.insert_result(
        filename="safe.jpg", stored_name="sample:safe.jpg",
        preview_name="safe.jpg", task_id="", result=safe,
    )
    client = app_module.app.test_client()

    rejected = client.post("/api/results/bulk-review", json={
        "ids": [unsafe_id], "review_status": "确认通过", "final_result": "通过",
    })
    accepted = client.post("/api/results/bulk-review", json={
        "ids": [safe_id], "review_status": "确认通过", "final_result": "通过",
    })

    assert rejected.status_code == 409
    assert rejected.get_json()["invalid"][0]["id"] == unsafe_id
    assert test_database.get_result(unsafe_id)["review_status"] == "待复核"
    assert accepted.status_code == 200
    assert accepted.get_json()[0]["review_status"] == "确认通过"


def test_human_seal_verdict_overrides_generic_text_similarity():
    current = {
        "fields": {
            "要求到货": "2025-10-24",
            "签章要求": "广州市知星通讯器材有限公司（盖椭圆的代码章）",
        },
        "field_metadata": {},
        "date_check": {},
        "seal_check": {},
    }

    updated = _apply_human_edits(current, {
        "actual_date": "2025-10-24",
        "seal_text": "代码：6092851",
        "truth_seal_should_match": True,
    })

    assert updated["seal_check"]["status"] == "匹配"
    assert updated["seal_check"]["score"] == 1.0
    assert updated["seal_check"]["human_confirmed_match"] is True
    assert updated["overall"] == "通过"


def test_product_cell_edit_retains_machine_value():
    current = {
        "fields": {"要求到货": "2025-01-05", "签章要求": "测试章", "商品明细原文": "10 | G1 | OCR物料"},
        "field_metadata": {},
        "product_table": {
            "columns": ["行号", "产品类别", "物料编号"],
            "rows": [{
                "values": {"行号": "10", "产品类别": "G1", "物料编号": "OCR物料"},
                "original_values": {"行号": "10", "产品类别": "G1", "物料编号": "OCR物料"},
                "confidences": {"行号": 1.0, "产品类别": 0.86, "物料编号": 0.7},
                "sources": {"行号": "OCR", "产品类别": "OCR", "物料编号": "OCR"},
                "low_confidence_columns": ["物料编号"],
            }],
        },
        "date_check": {"actual": "2025-01-05"},
        "seal_check": {"recognized": "测试章"},
    }
    updated = _apply_human_edits(current, {
        "product_rows": [{"row": 0, "column": "物料编号", "value": "人工确认物料"}],
        "actual_date": "2025-01-05",
        "seal_text": "测试章",
    })
    detail = updated["product_table"]["rows"][0]
    assert detail["values"]["物料编号"] == "人工确认物料"
    assert detail["original_values"]["物料编号"] == "OCR物料"
    assert detail["sources"]["物料编号"] == "人工复核"
    assert "物料编号" not in detail["low_confidence_columns"]


def test_confirmed_review_can_create_audited_ground_truth(tmp_path, monkeypatch):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    truth_path = tmp_path / "ground_truth.json"
    monkeypatch.setattr(app_module, "database", test_database)
    monkeypatch.setattr(app_module, "GROUND_TRUTH_PATH", truth_path)

    fields = {name: f"确认-{name}" for name in GROUND_TRUTH_FIELDS}
    fields["要求到货"] = "2025-01-05"
    fields["签章要求"] = "测试客户收货章"
    row = {name: f"值-{name}" for name in PRODUCT_COLUMNS}
    result = {
        "filename": "new-sample.jpg", "overall": "需人工复核",
        "review_status": "待复核", "final_result": "需人工复核",
        "fields": fields, "field_metadata": {},
        "product_table": {
            "columns": list(PRODUCT_COLUMNS),
            "rows": [{"values": row, "confidences": {}, "sources": {}}],
        },
        "date_check": {"actual": "2025-01-05"},
        "seal_check": {"recognized": "测试客户收货章"},
    }
    result_id = test_database.insert_result(
        filename=result["filename"], stored_name="sample:new-sample.jpg",
        preview_name="preview.jpg", task_id="", result=result,
    )
    payload = {
        "fields": fields,
        "product_rows": [
            {"row": 0, "column": name, "value": value} for name, value in row.items()
        ],
        "actual_date": "2025-01-05", "seal_text": "测试客户收货章",
        "review_status": "确认通过", "final_result": "通过",
        "save_ground_truth": True, "truth_seal_should_match": True,
        "human_note": "人工逐项核对",
    }
    client = app_module.app.test_client()
    response = client.patch(f"/api/results/{result_id}/review", json=payload)
    assert response.status_code == 200
    assert response.get_json()["ground_truth_saved"]["total"] == 1
    truth_response = client.get(f"/api/results/{result_id}/ground-truth").get_json()
    assert truth_response["exists"] is True
    assert truth_response["entry"]["actual_date"] == "2025-01-05"
    assert truth_response["history"][0]["action"] == "新增评测真值"


def test_accuracy_report_follows_requested_backend(tmp_path, monkeypatch):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    truth_path = tmp_path / "ground_truth.json"
    truth_path.write_text(json.dumps({
        "sample.jpg": {
            "fields": {"客户名称": "Vision 正确客户"}, "product_rows": [],
            "actual_date": "", "seal_should_match": False,
        }
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(app_module, "database", test_database)
    monkeypatch.setattr(app_module, "GROUND_TRUTH_PATH", truth_path)

    for backend, customer in (("vision", "Vision 正确客户"), ("hybrid", "混合错误客户")):
        task_id = f"task-{backend}"
        test_database.create_task(task_id, backend, 1, backend)
        result = sample_result("sample.jpg")
        result.update(ocr_backend=backend)
        result["fields"]["客户名称"] = customer
        test_database.insert_result(
            filename="sample.jpg", stored_name="sample:sample.jpg",
            preview_name=f"{backend}.jpg", task_id=task_id, result=result,
        )
        test_database.update_task(task_id, success=True, pending_review=True)

    payload = app_module.app.test_client().get(
        "/api/report?ocr_backend=vision"
    ).get_json()
    assert payload["accuracy"]["scope_backend"] == "vision"
    assert payload["accuracy"]["field_accuracy"] == 1


def test_paginated_receipt_is_reviewed_as_one_logical_record(tmp_path, monkeypatch):
    test_database = Database(tmp_path / "results.db")
    test_database.initialize()
    monkeypatch.setattr(app_module, "database", test_database)
    task_id = "paginated-task"
    test_database.create_task(task_id, "two page receipt", 2, "hybrid")

    def page(filename, kind, row_number):
        return {
            "filename": filename,
            "overall": "需人工复核",
            "review_status": "待复核",
            "final_result": "需人工复核",
            "document_type": {"type": kind, "label": kind},
            "fields": {
                "客户订单号": "7284571207",
                "要求到货": "2025-04-25",
                "签章要求": "北京罗凡尼科技发展有限公司",
            } if kind == "receipt" else {
                "签章要求": "北京罗凡尼科技发展有限公司",
            },
            "field_metadata": {},
            "product_table": {
                "columns": ["行号"],
                "rows": [{
                    "index": 0, "values": {"行号": row_number},
                    "original_values": {"行号": row_number},
                    "confidences": {"行号": .98}, "sources": {"行号": "OCR"},
                }],
                "confidence": .98, "source": "OCR 按列定位",
            },
            "date_check": {"actual": "", "status": "未识别", "confidence": 0},
            "seal_check": {"recognized": "", "status": "无法判断", "score": 0},
        }

    cover_id = test_database.insert_result(
        filename="7284571207.jpg", stored_name="sample:7284571207.jpg",
        preview_name="cover.jpg", task_id=task_id,
        result=page("7284571207.jpg", "receipt", "10"),
    )
    test_database.insert_result(
        filename="7284571207_01.jpg", stored_name="sample:7284571207_01.jpg",
        preview_name="continuation.jpg", task_id=task_id,
        result=page("7284571207_01.jpg", "product_continuation", "40"),
    )

    client = app_module.app.test_client()
    listed = client.get(f"/api/results?task_id={task_id}").get_json()
    assert len(listed) == 1
    assert [row["values"]["行号"] for row in listed[0]["product_table"]["rows"]] == ["10", "40"]

    detail = client.get(f"/api/results/{cover_id}").get_json()
    assert detail["document_type"]["label"] == "三星出库回单（2页）"
    response = client.patch(f"/api/results/{cover_id}/review", json={
        "fields": detail["fields"],
        "product_rows": [],
        "actual_date": "2025-04-24",
        "seal_text": "北京罗凡尼科技发展有限公司",
        "review_status": "确认不通过",
        "final_result": "不通过",
        "human_note": "分页人工确认",
    })
    assert response.status_code == 200
    reviewed = response.get_json()
    assert reviewed["review_status"] == "确认不通过"
    assert reviewed["date_check"]["actual"] == "2025-04-24"
    assert len(reviewed["product_table"]["rows"]) == 2

    reloaded = client.get(f"/api/results/{cover_id}").get_json()
    assert len(reloaded["product_table"]["rows"]) == 2
    assert reloaded["page_review_applied"] is True
