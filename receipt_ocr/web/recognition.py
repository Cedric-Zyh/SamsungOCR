"""HTTP handler for synchronous single-image recognition."""

from __future__ import annotations

from .dependencies import RecognitionDependencies

from pathlib import Path
import uuid

from flask import abort, jsonify, request
from werkzeug.utils import secure_filename

from receipt_ocr.application import resolve_recognition_options
from receipt_ocr.providers.catalog import backend_label


def analyze_upload(ctx: RecognitionDependencies):
    task_id = request.form.get("task_id", "").strip()
    if task_id:
        try:
            if ctx.database.get_task(task_id)["execution_mode"] == "queue":
                return jsonify(error="该任务请通过后台上传接口提交图片"), 409
        except KeyError:
            return jsonify(error="批量任务不存在"), 404
    upload = request.files.get("file")
    sample_name = request.form.get("sample", "").strip()
    source = None
    original_name = ""
    stored_name = ""
    if upload and upload.filename:
        original_name = Path(upload.filename).name
        safe_name = secure_filename(original_name) or f"receipt{Path(original_name).suffix}"
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ctx.allowed_extensions:
            return jsonify({"error": "仅支持 JPG、PNG、BMP、WEBP 图片"}), 400
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        source = ctx.upload_dir / stored_name
        upload.save(source)
    elif sample_name:
        source = (ctx.data_dir / Path(sample_name).name).resolve()
        if source.parent != ctx.data_dir.resolve() or not source.is_file():
            abort(404)
        original_name = source.name
        stored_name = f"sample:{source.name}"
    else:
        return jsonify({"error": "请选择图片"}), 400

    if task_id:
        try:
            task = ctx.database.get_task(task_id)
            options = resolve_recognition_options(
                recognition_config=task.get("recognition_config"),
                ocr_backend=task.get("ocr_backend"),
                seal_recognition_mode=task.get("seal_recognition_mode"),
                api_enabled=ctx.seal_api_enabled,
            )
        except KeyError:
            return jsonify({"error": "批量任务不存在"}), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        try:
            options = resolve_recognition_options(
                recognition_config=request.form.get("recognition_config", "null"),
                ocr_backend=request.form.get("ocr_backend", "auto"),
                seal_recognition_mode=request.form.get("seal_recognition_mode"),
                api_enabled=ctx.seal_api_enabled,
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        task_id = uuid.uuid4().hex
        ctx.database.create_task(
            task_id, original_name, 1, options.ocr_backend,
            options.seal_recognition_mode, options.recognition_config,
        )

    try:
        result, preview_name = ctx.documents.recognize(
            source,
            recognition_config=options.recognition_config,
            filename=original_name,
            ocr_backend=options.ocr_backend,
            seal_recognition_mode=options.seal_recognition_mode,
        )
        record_id = ctx.database.insert_result(
            filename=original_name,
            stored_name=stored_name,
            preview_name=preview_name,
            task_id=task_id,
            result=result,
        )
        result["id"] = record_id
        ctx.database.update_task(task_id, success=True, pending_review=result["review_status"] == "待复核")
        return jsonify(result)
    except Exception as exc:
        ctx.logger.exception("Receipt analysis failed")
        failure = {
            "filename": original_name,
            "overall": "识别失败",
            "final_result": "识别失败",
            "review_status": "待复核",
            "review_reasons": ["识别流程异常"],
            "ocr_backend": options.ocr_backend,
            "ocr_backend_label": backend_label(options.ocr_backend),
            "seal_recognition_mode": options.seal_recognition_mode,
            "fields": {},
            "field_metadata": {},
            "date_check": {"required": "", "actual": "", "status": "未识别", "confidence": 0},
            "seal_check": {"requirement": "", "recognized": "", "status": "未识别", "score": 0},
            "processing_artifacts": {"date": [], "seals": []},
            "processing_seconds": 0,
            "created_at": ctx.now_iso(),
            "updated_at": ctx.now_iso(),
        }
        record_id = ctx.database.insert_result(
            filename=original_name,
            stored_name=stored_name,
            preview_name="",
            task_id=task_id,
            result=failure,
            error_type="识别失败",
            error_message=str(exc),
        )
        ctx.database.update_task(task_id, success=False, pending_review=True)
        return jsonify({"error": f"识别失败：{exc}", "id": record_id}), 500
