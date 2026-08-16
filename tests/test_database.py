from pathlib import Path

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
