from pathlib import Path

import pytest

from receipt_ocr.database import Database


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


def test_review_keeps_immutable_machine_result(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    task = database.create_task("task", "测试", 1, "paddle")
    assert task["total"] == 1
    assert task["ocr_backend"] == "paddle"
    original = sample_result()
    result_id = database.insert_result(
        filename=original["filename"], stored_name="sample:7266301052.jpg",
        preview_name="preview.jpg", task_id="task", result=original,
    )
    database.update_task("task", success=True, pending_review=True)
    assert database.get_task("task")["pending_review"] == 1
    edited = sample_result()
    edited["fields"]["客户名称"] = "人工客户"
    reviewed = database.review_result(
        result_id, result=edited, review_status="确认通过", final_result="通过",
        note="人工核对原件", action="确认通过", error_type="字段识别错误",
    )
    assert reviewed["fields"]["客户名称"] == "人工客户"
    assert reviewed["review_status"] == "确认通过"
    assert database.get_task("task")["pending_review"] == 0
    assert database.list_original_results()[0]["fields"]["客户名称"] == "测试客户"
    assert database.history(result_id)[0]["action"] == "确认通过"


def test_task_persists_seal_recognition_mode(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()

    task = database.create_task(
        "seal-task", "清瞳印章识别", 1, "paddle", "qingtong"
    )

    assert task["seal_recognition_mode"] == "qingtong"


def test_retry_updates_batch_pending_review_count(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("task", "测试", 1, "hybrid")
    original = sample_result()
    original.update(review_status="确认通过", final_result="通过", overall="通过")
    result_id = database.insert_result(
        filename=original["filename"], stored_name="sample:x",
        preview_name="before.jpg", task_id="task", result=original,
    )
    database.update_task("task", success=True, pending_review=False)

    retried = sample_result()
    database.replace_after_retry(result_id, retried, "after.jpg")

    assert database.get_task("task")["pending_review"] == 1


def test_initialize_reconciles_legacy_stale_batch_pending_count(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("task", "测试", 1, "hybrid")
    original = sample_result()
    result_id = database.insert_result(
        filename=original["filename"], stored_name="sample:x",
        preview_name="before.jpg", task_id="task", result=original,
    )
    database.update_task("task", success=True, pending_review=True)
    with database.connect() as connection:
        connection.execute(
            "UPDATE results SET review_status='确认通过' WHERE id=?", (result_id,)
        )

    database.initialize()

    assert database.get_task("task")["pending_review"] == 0


def test_order_filter_searches_long_order_and_tracking_number(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    result = sample_result()
    result["fields"]["运单号"] = "W20250103-011373"
    database.insert_result(
        filename=result["filename"], stored_name="sample:x", preview_name="x.jpg",
        task_id="", result=result,
    )
    assert database.list_results(filters={"order_id": "007266301052"})
    assert database.list_results(filters={"order_id": "011373"})


def test_latest_by_filename_keeps_one_current_row_and_filters_after_collapse(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    stale = sample_result()
    stale["fields"]["客户名称"] = "旧客户"
    current = sample_result()
    current["fields"]["客户名称"] = "新客户"
    database.insert_result(
        filename=stale["filename"], stored_name="sample:old", preview_name="old.jpg",
        task_id="", result=stale,
    )
    database.insert_result(
        filename=current["filename"], stored_name="sample:new", preview_name="new.jpg",
        task_id="", result=current,
    )

    latest = database.list_results(latest_by_filename=True)

    assert len(latest) == 1
    assert latest[0]["fields"]["客户名称"] == "新客户"
    assert database.list_results(
        latest_by_filename=True, filters={"customer": "旧客户"}
    ) == []


def test_latest_by_filename_is_scoped_to_selected_ocr_backend(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    hybrid = sample_result()
    hybrid["ocr_backend"] = "hybrid"
    hybrid["fields"]["客户名称"] = "Hybrid客户"
    server = sample_result()
    server["ocr_backend"] = "paddle_server"
    server["fields"]["客户名称"] = "Server实验客户"
    database.insert_result(
        filename=hybrid["filename"], stored_name="sample:hybrid",
        preview_name="hybrid.jpg", task_id="", result=hybrid,
    )
    database.insert_result(
        filename=server["filename"], stored_name="sample:server",
        preview_name="server.jpg", task_id="", result=server,
    )

    selected = database.list_results(
        latest_by_filename=True, filters={"ocr_backend": "hybrid"}
    )

    assert len(selected) == 1
    assert selected[0]["fields"]["客户名称"] == "Hybrid客户"


def test_ground_truth_change_has_local_audit_history(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    result = sample_result("new-sample.jpg")
    result_id = database.insert_result(
        filename=result["filename"], stored_name="sample:new-sample.jpg",
        preview_name="preview.jpg", task_id="", result=result,
    )
    first = database.record_ground_truth_change(
        filename=result["filename"], result_id=result_id,
        before=None, after={"actual_date": "2025-01-05"}, note="首次标注",
    )
    second = database.record_ground_truth_change(
        filename=result["filename"], result_id=result_id,
        before={"actual_date": "2025-01-05"},
        after={"actual_date": "2025-01-06"}, note="复查原件",
    )
    history = database.ground_truth_history(result["filename"])
    assert first["action"] == "新增评测真值"
    assert second["action"] == "更新评测真值"
    assert [row["note"] for row in history] == ["复查原件", "首次标注"]


def test_accuracy_source_excludes_partially_completed_batch(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("complete", "完成批次", 1, "hybrid")
    complete = sample_result("sample.jpg")
    complete["fields"]["客户名称"] = "完成结果"
    database.insert_result(
        filename="sample.jpg", stored_name="sample:sample.jpg", preview_name="a.jpg",
        task_id="complete", result=complete,
    )
    database.update_task("complete", success=True, pending_review=False)

    database.create_task("running", "处理中批次", 2, "hybrid_server")
    running = sample_result("sample.jpg")
    running["fields"]["客户名称"] = "未完成结果"
    database.insert_result(
        filename="sample.jpg", stored_name="sample:sample.jpg", preview_name="b.jpg",
        task_id="running", result=running,
    )
    database.update_task("running", success=True, pending_review=False)

    source = database.list_original_results(completed_tasks_only=True)
    assert len(source) == 1
    assert source[0]["fields"]["客户名称"] == "完成结果"


def test_restart_recovers_unfinished_batch_as_interrupted(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("crashed", "Server 大模型批次", 3, "paddle_server")
    database.update_task("crashed", success=True, pending_review=False)

    assert database.recover_interrupted_tasks() == 1
    task = database.get_task("crashed")
    assert task["status"] == "已中断"
    assert task["completed"] == 3
    assert task["succeeded"] == 1
    assert task["failed"] == 2
    assert task["pending_review"] == 2
    assert "异常退出" in task["error_message"]
    assert database.list_original_results(completed_tasks_only=True) == []
    database.initialize()
    assert database.get_task("crashed")["pending_review"] == 2


def test_restart_recovery_does_not_touch_completed_batch(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("done", "完成批次", 1, "paddle")
    database.update_task("done", success=True, pending_review=False)
    assert database.recover_interrupted_tasks() == 0
    assert database.get_task("done")["status"] == "已完成"


def test_late_worker_cannot_revive_interrupted_batch_or_overrun_counters(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("crashed", "中断后仍有迟到结果", 2, "hybrid")
    database.update_task("crashed", success=True, pending_review=True)
    database.recover_interrupted_tasks()

    recovered = database.get_task("crashed")
    assert recovered["status"] == "已中断"
    assert recovered["completed"] == 2

    late = database.update_task("crashed", success=True, pending_review=True)
    assert late["status"] == "已中断"
    assert late["completed"] == 2
    assert late["succeeded"] == recovered["succeeded"]
    assert late["failed"] == recovered["failed"]


def test_completed_batch_ignores_duplicate_worker_update(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    database.create_task("done", "完成批次", 1, "paddle")
    database.update_task("done", success=True, pending_review=False)

    duplicate = database.update_task("done", success=True, pending_review=False)
    assert duplicate["status"] == "已完成"
    assert duplicate["completed"] == 1
    assert duplicate["succeeded"] == 1


def test_unreliable_machine_rejection_is_audited_and_queued(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    result = sample_result("uncertain.jpg")
    result.update(overall="不通过", final_result="不通过", review_status="无需复核")
    result["date_check"] = {"status": "匹配", "reliable": True}
    result["seal_check"] = {"status": "不匹配", "reliable": False}
    result_id = database.insert_result(
        filename=result["filename"], stored_name="sample:uncertain.jpg",
        preview_name="x.jpg", task_id="", result=result,
    )
    assert database.enforce_uncertain_review_queue() == 1
    updated = database.get_result(result_id)
    assert updated["overall"] == "需人工复核"
    assert updated["review_status"] == "待复核"
    assert "印章内容无法可靠判断" in updated["review_reasons"]
    assert database.history(result_id)[0]["action"] == "安全规则升级"
    assert database.list_original_results()[0]["overall"] == "不通过"
    assert database.enforce_uncertain_review_queue() == 0


def test_legacy_reliable_date_mismatch_is_migrated_to_review(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    result = sample_result("legacy-date-mismatch.jpg")
    result.update(overall="不通过", final_result="不通过", review_status="无需复核")
    result["date_check"] = {"status": "不匹配", "reliable": True}
    result["seal_check"] = {"status": "匹配", "reliable": True}
    result_id = database.insert_result(
        filename=result["filename"], stored_name="sample:legacy-date-mismatch.jpg",
        preview_name="x.jpg", task_id="", result=result,
    )

    assert database.enforce_uncertain_review_queue() == 1
    updated = database.get_result(result_id)
    assert updated["overall"] == updated["final_result"] == "需人工复核"
    assert updated["review_status"] == "待复核"
    assert database.history(result_id)[0]["action"] == "安全规则升级"


def test_legacy_provider_timeout_is_migrated_to_failed(tmp_path: Path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    result = sample_result("provider-timeout.jpg")
    result["review_reasons"] = ["印刷字段 / danzhengtong：单证通等待识别结果超时（60 秒），已停止查询"]
    result_id = database.insert_result(
        filename=result["filename"], stored_name="sample:provider-timeout.jpg",
        preview_name="x.jpg", task_id="", result=result,
    )

    assert database.enforce_uncertain_review_queue() == 1
    updated = database.get_result(result_id)
    assert updated["overall"] == updated["final_result"] == "识别失败"
    assert updated["review_status"] == "无需复核"
    assert database.history(result_id)[0]["action"] == "失败分类修复"
    assert database.enforce_uncertain_review_queue() == 0


def test_import_day_scopes_before_dedup_and_survives_retry(tmp_path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    ids = []
    for day in ("2026-09-08", "2026-09-09"):
        item = sample_result("same.jpg")
        item.update(created_at=f"{day}T23:59:00+08:00", ocr_backend="hybrid")
        item["date_check"]["actual"] = "2025-01-05"
        ids.append(database.insert_result(
            filename="same.jpg", stored_name="same.jpg", preview_name="", task_id="", result=item,
        ))
    retried = sample_result("same.jpg")
    retried.update(ocr_backend="hybrid")
    retried["date_check"]["actual"] = "2025-01-05"
    database.replace_after_retry(ids[0], retried, "")
    scope = {"import_date": "2026-09-08", "customer": "测试", "date": "2025-01-05"}
    results = database.list_results(filters=scope, latest_by_filename=True)
    assert [item["id"] for item in results] == [ids[0]]
    assert results[0]["created_at"].startswith("2026-09-08")
    assert not database.list_results(filters={**scope, "overall": "通过"}, latest_by_filename=True)
    assert [item["id"] for item in database.list_results(
        filters={"import_date": "2026-09-09"}, latest_by_filename=True
    )] == [ids[1]]
    assert not database.list_results(filters={"import_date": "2026-09-10"}, latest_by_filename=True)


def test_import_calendar_counts_days_and_backend_without_duplicate_runs(tmp_path):
    database = Database(tmp_path / "results.db")
    database.initialize()
    for filename, day, backend in [
        ("a.jpg", "2026-09-08", "hybrid"),
        ("a.jpg", "2026-09-08", "hybrid"),
        ("b.jpg", "2026-09-08", "hybrid"),
        ("a.jpg", "2026-09-09", "hybrid"),
        ("c.jpg", "2026-09-09", "paddle"),
        ("d.jpg", "2026-08-31", "hybrid"),
    ]:
        result = sample_result(filename)
        result.update(created_at=f"{day}T12:00:00+08:00", ocr_backend=backend)
        database.insert_result(filename=filename, stored_name=filename, preview_name="", task_id="", result=result)
    assert database.import_date_counts("2026-09", "hybrid") == {"2026-09-08": 2, "2026-09-09": 1}
    assert database.import_date_counts("2026-09", "paddle") == {"2026-09-09": 1}
    assert database.import_date_counts("2026-10", "hybrid") == {}


def test_header_filters_prefix_and_reliability_status(tmp_path):
    database = Database(tmp_path / 'header-filters.db')
    database.initialize()
    for filename, reliable in [('7123.jpg', True), ('8172.jpg', False), ('7281.jpg', False)]:
        database.insert_result(filename=filename, stored_name=filename, preview_name='', task_id='', result={
            'fields': {'客户名称': '合肥示例有限公司'}, 'internal_fields': {'客户订单号': '8172', '运单号': '71234'},
            'seal_check': {'status': '匹配', 'reliable': reliable}, 'date_check': {'status': '未识别'},
            'overall': '需人工复核'})
    rows = database.list_results(filters={'filename': '7', 'text_match': 'prefix'}, latest_by_filename=True)
    assert {r['filename'] for r in rows} == {'7123.jpg', '7281.jpg'}
    assert len(database.list_results(filters={'filename': '7'})) == 3  # Other callers retain contains search.
    assert len(database.list_results(filters={'order_id': '7', 'text_match': 'prefix'})) == 3
    assert not database.list_results(filters={'order_id': '123', 'text_match': 'prefix'})
    assert len(database.list_results(filters={'search_prefix': '7'})) == 3
    assert [r['filename'] for r in database.list_results(filters={'search_prefix': '728'})] == ['7281.jpg']
    assert not database.list_results(filters={'search_prefix': '123'})
    assert len(database.list_results(filters={'customer': '示例', 'text_match': 'prefix'})) == 3
    matched = database.list_results(filters={'seal_status': '匹配'})
    assert [r['filename'] for r in matched] == ['7123.jpg']
    assert len(database.list_results(filters={'seal_status': '匹配待确认', 'date_status': '未识别'})) == 2
    assert not database.list_results(filters={'date_status': '匹配'})


def test_folder_import_keeps_same_names_and_searches_image_prefix(tmp_path):
    database = Database(tmp_path / 'folder.db')
    database.initialize()
    for filename in ['回单/甲/7123.jpg', '回单/乙/7123.jpg', '回单/乙/8172.jpg']:
        database.insert_result(filename=filename, stored_name=filename, preview_name='', task_id='',
                               result={'overall': '需人工复核'})
    rows = database.list_results(filters={'search_prefix': '7'}, latest_by_filename=True)
    assert {r['filename'] for r in rows} == {'回单/甲/7123.jpg', '回单/乙/7123.jpg'}


def add_two_page_receipt(database, task_id="task", *, backend="vision", day="2026-09-10"):
    database.create_task(task_id, task_id, 2)
    ids = []
    for suffix, kind, row in (("", "receipt", "10"), ("_01", "product_continuation", "40")):
        filename = "folder/receipt" + suffix + ".jpg"
        payload = {
            **sample_result(filename), "ocr_backend": backend,
            "created_at": day + "T12:00:00+08:00", "document_type": {"type": kind},
            "fields": {"客户订单号": "ORDER-123", "客户名称": "测试客户", "要求到货": "2026-09-10"}
                if kind == "receipt" else {"签章要求": "客户章"},
            "product_table": {"columns": ["行号"], "rows": [{"values": {"行号": row}}]},
            "date_check": {"actual": "2026-09-10", "confidence": .99, "reliable": True}
                if kind == "product_continuation" else {"actual": "", "status": "未识别"},
        }
        ids.append(database.insert_result(filename=filename, stored_name=filename, preview_name="",
                                          task_id=task_id, result=payload))
    return ids


@pytest.mark.parametrize("filters", [
    {"order_id": "ORDER-123"}, {"customer": "测试客户"}, {"date": "2026-09-10"},
    {"filename": "receipt_01"}, {"filename": "folder/receipt_01", "text_match": "prefix"},
    {"search_prefix": "receipt_01"}, {"task_id": "task", "import_date": "2026-09-10"},
])
def test_query_receipts_filters_complete_receipt_and_matches_continuation_filename(tmp_path, filters):
    database = Database(tmp_path / "query.db")
    database.initialize()
    cover_id, _ = add_two_page_receipt(database)

    response = database.query_receipts(filters=filters, latest_by_filename=True, limit=1)
    assert response["total"] == 1
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["id"] == cover_id
    assert [row["values"]["行号"] for row in item["product_table"]["rows"]] == ["10", "40"]
    assert item["fields"]["签章要求"] == "客户章"


def test_query_receipts_paginates_logical_rows_without_cutting_pages(tmp_path):
    database = Database(tmp_path / "query.db")
    database.initialize()
    cover_id, _ = add_two_page_receipt(database)
    single = database.insert_result(filename="new.jpg", stored_name="new", preview_name="", task_id="",
                                    result=sample_result("new.jpg"))

    first = database.query_receipts(limit=1)
    second = database.query_receipts(limit=1, offset=1)
    assert first["total"] == second["total"] == 2
    assert [r["id"] for r in first["items"]] == [single]
    assert [r["id"] for r in second["items"]] == [cover_id]
    assert len(second["items"][0]["product_table"]["rows"]) == 2
    assert database.query_receipts(limit=0) == {"items": [], "total": 2}
    assert database.query_receipts(offset=2) == {"items": [], "total": 2}


def test_query_receipts_latest_does_not_duplicate_resubmitted_continuation(tmp_path):
    database = Database(tmp_path / "resubmit.db")
    database.initialize()
    cover, continuation = add_two_page_receipt(database)
    updated = database.get_result(continuation)
    updated["product_table"]["rows"] = [{"values": {"行号": "50"}}]
    database.insert_result(filename=updated["filename"], stored_name="new", preview_name="",
                           task_id="task", result=updated)

    response = database.query_receipts(latest_by_filename=True)
    assert response["total"] == 1
    assert response["items"][0]["id"] == cover
    assert [row["values"]["行号"] for row in response["items"][0]["product_table"]["rows"]] == ["10", "50"]


def test_query_receipts_backend_scope_precedes_logical_filename_dedup(tmp_path):
    database = Database(tmp_path / "query.db")
    database.initialize()
    first, _ = add_two_page_receipt(database, "production", backend="hybrid")
    latest, _ = add_two_page_receipt(database, "experiment", backend="paddle")

    response = database.query_receipts(filters={"ocr_backend": "hybrid"}, latest_by_filename=True)
    assert response["total"] == 1
    assert [r["id"] for r in response["items"]] == [first]
    assert len(response["items"][0]["product_table"]["rows"]) == 2
    assert [r["id"] for r in database.query_receipts(latest_by_filename=True)["items"]] == [latest]
    assert database.query_receipts()["total"] == 2


def test_query_receipts_keeps_companion_page_with_different_backend(tmp_path):
    database = Database(tmp_path / "query.db")
    database.initialize()
    cover, continuation = add_two_page_receipt(database)
    updated = database.get_result(continuation)
    updated["ocr_backend"] = "paddle"
    database.replace_after_retry(continuation, updated, "")

    response = database.query_receipts(filters={"ocr_backend": "vision"}, latest_by_filename=True)
    assert response["items"][0]["id"] == cover
    assert len(response["items"][0]["product_table"]["rows"]) == 2


def test_query_receipts_includes_continuation_when_legacy_import_crosses_midnight(tmp_path):
    database = Database(tmp_path / "midnight.db")
    database.initialize()
    cover, continuation = add_two_page_receipt(database)
    with database.connect() as connection:
        connection.execute("UPDATE results SET created_at=? WHERE id=?", ("2026-09-11T00:01:00+08:00", continuation))

    response = database.query_receipts(filters={"import_date": "2026-09-10"}, latest_by_filename=True)
    assert response["items"][0]["id"] == cover
    assert len(response["items"][0]["product_table"]["rows"]) == 2
    assert database.query_receipts(filters={"import_date": "2026-09-11"})["total"] == 0


def test_initialize_backfills_page_keys_for_preexisting_database(tmp_path):
    import json
    import sqlite3
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE results (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
            filename TEXT NOT NULL, stored_name TEXT NOT NULL, preview_name TEXT NOT NULL,
            overall TEXT NOT NULL, result_json TEXT NOT NULL)""")
        connection.execute("INSERT INTO results VALUES(1,?,?,?,?,?,?)", (
            "2026-09-10T12:00:00+08:00", "folder/r_01.jpg", "r", "", "需人工复核", json.dumps(sample_result())))
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        assert connection.execute("SELECT page_key FROM results WHERE id=1").fetchone()[0] == "folder/r"


def test_review_result_shared_transaction_rolls_back_all_reviews_and_history(tmp_path):
    database = Database(tmp_path / "atomic.db")
    database.initialize()
    ids = [database.insert_result(filename=name, stored_name=name, preview_name="", task_id="",
                                  result=sample_result(name)) for name in ("first.jpg", "second.jpg")]
    snapshots = [database.get_result(i) for i in ids]
    with pytest.raises(KeyError):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for result_id, original in zip(ids + [99999], snapshots + [sample_result()]):
                database.review_result(result_id, result=original, review_status="确认不通过",
                                       final_result="不通过", note="", action="批量确认", _connection=connection)
    assert [database.get_result(i)["review_status"] for i in ids] == ["待复核", "待复核"]
    assert database.all_history() == []
