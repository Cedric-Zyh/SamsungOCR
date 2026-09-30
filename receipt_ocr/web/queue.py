"""HTTP handlers for batch tasks and the background recognition queue.

The handlers receive the application module as a context.  This keeps the
runtime objects live, so tests and deployments can replace the database,
storage paths, or worker without rebuilding a second dependency container.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import uuid

from flask import abort, jsonify, request

from receipt_ocr.application import resolve_recognition_options
from receipt_ocr.storage import ReceiptFileStore


def create_task(ctx):
    payload = request.get_json(silent=True) or {}
    total = payload.get("total", 0)
    if type(total) is not int:
        return jsonify(error="批量任务数量必须是整数"), 400
    if total <= 0 or total > 5000:
        return jsonify({"error": "批量任务数量必须在 1 至 5000 之间"}), 400
    try:
        options = resolve_recognition_options(
            recognition_config=payload.get("recognition_config"),
            ocr_backend=payload.get("ocr_backend"),
            seal_recognition_mode=payload.get("seal_recognition_mode"),
            api_enabled=ctx.analyzer.seal_api.enabled,
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    task_id = uuid.uuid4().hex
    if payload.get("background"):
        items = payload.get("items")
        if (not isinstance(items, list) or len(items) != total or
            any(not isinstance(item, dict) or not isinstance(item.get("filename"), str)
                or not item["filename"].strip() or len(item["filename"]) > 500 for item in items)):
            return jsonify(error="请提供与上传数量一致的图片清单"), 400
        return jsonify(ctx.job_store.create_batch(task_id, str(payload.get("name") or "批量识别"), items,
            options.as_dict())), 201
    return jsonify(ctx.database.create_task(
        task_id, str(payload.get("name") or "批量识别"), total,
        options.ocr_backend, options.seal_recognition_mode,
        options.recognition_config,
    ))


def list_tasks(ctx):
    return jsonify(ctx.database.list_tasks())


def get_task(ctx, task_id: str):
    try:
        task = ctx.database.get_task(task_id)
        return jsonify(ctx.job_store.task(task_id) if task["execution_mode"] == "queue" else task)
    except KeyError:
        abort(404)


def _wake_jobs(ctx):
    if ctx.job_worker is not None:
        ctx.job_worker.start()


def queue_progress(ctx):
    day = request.args.get("import_date", "")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        return jsonify(error="请选择有效的导入日期"), 400
    return jsonify(ctx.job_store.daily(day))


def queue_control(ctx, wake=None):
    if request.method == "GET":
        return jsonify(ctx.job_store.control())
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("paused"), bool):
        return jsonify(error="请指定是否暂停识别"), 400
    control = ctx.job_store.set_paused(payload["paused"])
    if not control["paused"]:
        (wake or (lambda: _wake_jobs(ctx)))()
    return jsonify(control)


def upload_job(ctx, job_id, wake=None):
    try:
        job = ctx.job_store.get(job_id)
    except KeyError:
        abort(404)
    if job["status"] != "awaiting_upload":
        return jsonify(ctx.job_store.public(job)), 200
    upload = request.files.get("file")
    sample = request.form.get("sample", "")
    saved_path = None
    if upload and upload.filename:
        expected = Path(job["filename"].replace("\\", "/")).name
        if Path(upload.filename.replace("\\", "/")).name != expected:
            return jsonify(error=f"请选择原任务中的图片：{expected}"), 400
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ctx.ALLOWED_EXTENSIONS:
            return jsonify(error="仅支持 JPG、PNG、BMP、WEBP 图片"), 400
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        saved_path = ctx.UPLOAD_DIR / stored_name
        temporary = ctx.UPLOAD_DIR / f"{stored_name}.part"
        try:
            upload.save(temporary)
            if temporary.stat().st_size == 0:
                return jsonify(error="图片为空，请重新选择"), 400
            temporary.replace(saved_path)
        finally:
            temporary.unlink(missing_ok=True)
    elif sample:
        source = (ctx.DATA_DIR / Path(sample).name).resolve()
        if source.parent != ctx.DATA_DIR.resolve() or not source.is_file() or source.suffix.lower() not in ctx.ALLOWED_EXTENSIONS:
            abort(404)
        if source.name != Path(job["filename"].replace("\\", "/")).name:
            return jsonify(error="样单与任务清单不一致"), 400
        stored_name = f"sample:{source.name}"
    else:
        return jsonify(error="请选择图片"), 400
    try:
        job, accepted = ctx.job_store.accept_upload(job_id, stored_name)
    except Exception:
        if saved_path:
            saved_path.unlink(missing_ok=True)
        raise
    if not accepted and saved_path:
        saved_path.unlink(missing_ok=True)
    if job["start_requested"]:
        (wake or (lambda: _wake_jobs(ctx)))()
    return jsonify(ctx.job_store.public(job)), 202


def start_jobs(ctx, wake=None):
    payload = request.get_json(silent=True)
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not ids or len(ids) > 5000
            or any(not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 100 for job_id in ids)):
        return jsonify(error="请选择有效的待开始回单"), 400
    try:
        started = ctx.job_store.start_jobs(ids)
    except KeyError:
        return jsonify(error="所选回单不存在"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    (wake or (lambda: _wake_jobs(ctx)))()
    return jsonify(**started, control=ctx.job_store.control()), 202


def retry_job(ctx, job_id, wake=None):
    try:
        job = ctx.job_store.retry_failed(job_id)
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    (wake or (lambda: _wake_jobs(ctx)))()
    return jsonify(ctx.job_store.public(job)), 202


def _cleanup_cancelled_upload(ctx, job):
    ReceiptFileStore(ctx.DATA_DIR, ctx.UPLOAD_DIR).cleanup_cancelled_upload(job)


def cancel_job(ctx, job_id):
    try:
        job, deleted = ctx.job_store.delete_job(job_id)
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    if not deleted:
        return jsonify(error="这条回单已经完成，不能取消"), 409
    _cleanup_cancelled_upload(ctx, job)
    return jsonify(deleted=True, job_id=job_id), 200


def cancel_jobs(ctx):
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") if isinstance(payload, dict) else None
    try:
        result = ctx.job_store.delete_jobs(ids)
    except KeyError:
        return jsonify(error="所选待处理回单中有任务已不存在，请刷新后重试"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    for job in result["deleted"]:
        _cleanup_cancelled_upload(ctx, job)
    return jsonify(deleted=True, deleted_ids=[job["id"] for job in result["deleted"]],
                   kept_ids=[job["id"] for job in result["kept"]]), 200


def process_history(ctx):
    job_id = request.args.get("job_id", "").strip()
    result_id_raw = request.args.get("result_id", "").strip()
    result_id = 0
    if result_id_raw:
        try:
            result_id = int(result_id_raw)
        except ValueError:
            return jsonify(error="请选择有效的回单记录"), 400
    if not job_id and not result_id:
        return jsonify(error="请选择需要查看流程的回单"), 400
    try:
        return jsonify(ctx.job_store.process_history(job_id=job_id, result_id=result_id))
    except KeyError:
        abort(404)
