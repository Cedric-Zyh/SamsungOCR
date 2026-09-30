from __future__ import annotations

import json
import hashlib
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory, url_for
from werkzeug.utils import secure_filename

from receipt_ocr.application.analyzer import ReceiptAnalyzer
from receipt_ocr.application import RecognitionService, resolve_recognition_options
from receipt_ocr.application.pipeline import complete_result
from receipt_ocr.review import (
    apply_human_edits as _apply_human_edits,
    confirmation_error as _confirmation_error,
    prepare_review_payload,
)
from receipt_ocr.domain.fields.schema import OUTPUT_FIELDS, PRINTED_FIELDS, HANDWRITTEN_FIELDS, derive_signature_check, recognition_fields
from receipt_ocr.persistence.database import (
    DEFAULT_RECOGNITION_SETTINGS,
    DEFAULT_RETENTION_DAYS,
    Database,
    now_iso,
)
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.jobs.worker import JobWorker
from receipt_ocr.jobs.service import ReceiptJobService
from receipt_ocr.evaluation import (
    build_ground_truth_entry,
    evaluate_backends,
    evaluate_results,
    load_ground_truth,
    operational_report,
    save_ground_truth_entry,
)
from receipt_ocr.providers.catalog import backend_catalog, backend_label, default_backend, ocr_engine
from receipt_ocr.recognition.seal.policy import describe_secondary_read
from receipt_ocr.recognition.seal.reference.matcher import SealReferenceMatcher
from receipt_ocr.recognition.seal.providers.qingtong import render_selected_seal
from receipt_ocr.recognition.seal.api import (
    SEAL_RECOGNITION_MODES,
)
from receipt_ocr.recognition.seal.orientation import SEAL_ORIENTATION_MODES
from receipt_ocr.runtime import build_runtime_paths
from receipt_ocr.storage import ReceiptFileStore
from receipt_ocr.web.maintenance import StorageMaintenance
from receipt_ocr.web import queue as queue_handlers
from receipt_ocr.web import recognition as recognition_handlers
from receipt_ocr.web import results as result_handlers
from receipt_ocr.web import review as review_handlers
from receipt_ocr.web.settings import normalize_recognition_settings as _normalize_recognition_settings


BASE_DIR = Path(__file__).resolve().parents[2]
RUNTIME_PATHS = build_runtime_paths(BASE_DIR)
RESOURCE_DIR = RUNTIME_PATHS.resource_dir
DATA_DIR = RUNTIME_PATHS.data_dir
STORAGE_DIR = RUNTIME_PATHS.storage_dir
UPLOAD_DIR = RUNTIME_PATHS.upload_dir
PREVIEW_DIR = RUNTIME_PATHS.preview_dir
ARTIFACT_DIR = RUNTIME_PATHS.artifact_dir
EXPORT_DIR = RUNTIME_PATHS.export_dir
DATABASE_PATH = RUNTIME_PATHS.database_path
GROUND_TRUTH_PATH = RUNTIME_PATHS.ground_truth_path
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
RESULT_FILTERS = (
    "filename", "order_id", "customer", "date", "import_date", "overall", "review_status",
    "text_match", "search_prefix", "search", "date_status", "seal_status", "task_id", "ocr_backend",
)
app = Flask(
    __name__,
    static_folder=str(RESOURCE_DIR / "static"),
    template_folder=str(RESOURCE_DIR / "templates"),
)
app.config.update(MAX_CONTENT_LENGTH=500 * 1024 * 1024, JSON_AS_ASCII=False,
                  BACKGROUND_WORKER=True, TEMPLATES_AUTO_RELOAD=True)


@app.template_global()
def asset_url(filename: str) -> str:
    """Return a cache-busted URL for a static asset based on its mtime."""
    path = Path(app.static_folder or '') / filename
    try:
        version = path.stat().st_mtime_ns
    except OSError:
        version = 0
    return url_for('static', filename=filename, v=version)


analyzer = ReceiptAnalyzer()
database = Database(DATABASE_PATH)
seal_reference_matcher = SealReferenceMatcher(ARTIFACT_DIR)
recognition_service = RecognitionService(analyzer, seal_reference_matcher)
_initialized = False
_initialization_lock = threading.Lock()
_retention_cleanup_lock = threading.Lock()
_last_retention_cleanup = 0.0
job_store = JobStore(database)
job_worker = None
maintenance = StorageMaintenance(
    database,
    upload_dir=UPLOAD_DIR,
    preview_dir=PREVIEW_DIR,
    artifact_dir=ARTIFACT_DIR,
    export_dir=EXPORT_DIR,
    logger=app.logger,
)


def _purge_expired_data() -> dict:
    """Compatibility wrapper for the application lifecycle and tests."""
    maintenance.database = database
    maintenance.upload_dir = UPLOAD_DIR
    maintenance.preview_dir = PREVIEW_DIR
    maintenance.artifact_dir = ARTIFACT_DIR
    maintenance.export_dir = EXPORT_DIR
    maintenance.logger = app.logger
    return maintenance.purge_expired()


def _maybe_purge_expired_data() -> None:
    """Keep a long-running server within the retention window without scanning on every request."""
    global _last_retention_cleanup
    now = time.monotonic()
    if now - _last_retention_cleanup < 3600:
        return
    with _retention_cleanup_lock:
        now = time.monotonic()
        if now - _last_retention_cleanup < 3600:
            return
        _purge_expired_data()
        _last_retention_cleanup = now


def initialize(*, start_worker: bool = True) -> None:
    global _initialized, job_store, job_worker, _last_retention_cleanup
    if job_worker is not None:
        raise RuntimeError('后台任务已启动，请勿重复初始化服务')
    for directory in (UPLOAD_DIR, PREVIEW_DIR, ARTIFACT_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    database.initialize()
    job_store = JobStore(database)
    job_store.initialize()
    _purge_expired_data()
    _last_retention_cleanup = time.monotonic()
    database.recover_interrupted_tasks()
    database.enforce_uncertain_review_queue()
    seal_reference_matcher.refresh(
        database, load_ground_truth(GROUND_TRUTH_PATH)
    )
    _initialized = True
    if start_worker and app.config['BACKGROUND_WORKER']:
        service = ReceiptJobService(_configured_analyze,
            data_dir=DATA_DIR, upload_dir=UPLOAD_DIR, preview_dir=PREVIEW_DIR, artifact_dir=ARTIFACT_DIR)
        job_worker = JobWorker(job_store, service, logger=app.logger)
        job_worker.start()


def ensure_initialized() -> None:
    # Importing this module must not recover tasks or write to the real DB.
    # WSGI/Flask CLI initialize on their first request; direct launch below
    # initializes before accepting requests.
    if not _initialized:
        with _initialization_lock:
            if not _initialized:
                initialize()
    _maybe_purge_expired_data()


def _configured_analyze(*args, recognition_config=None, previous_fields=None, **kwargs):
    return recognition_service.recognize(
        *args,
        recognition_config=recognition_config,
        previous_fields=previous_fields,
        **kwargs,
    )


def _apply_visual_seal_reference(result: dict) -> dict:
    # Compatibility for saved-result audit tools. Live recognition finalizes
    # inside the pipeline, with the original filename supplied before matching.
    return complete_result(result, reference_matcher=seal_reference_matcher)


def index():
    # “一键测试”使用已有人工标注的基准集；目录中的其余新增图片仍可通过
    # “选择文件夹”批量导入，避免把数百张未标注图片误称为 6 张测试样单。
    samples = [name for name in load_ground_truth(GROUND_TRUTH_PATH) if (DATA_DIR / name).is_file()]
    return render_template(
        "index.html",
        samples=samples,
        output_fields=OUTPUT_FIELDS, printed_fields=PRINTED_FIELDS, handwritten_fields=HANDWRITTEN_FIELDS,
        seal_api_enabled=analyzer.seal_api.enabled,
        python_path=sys.executable,
        default_ocr_backend=default_backend(),
        ocr_backend_catalog=backend_catalog(),
        seal_recognition_modes=SEAL_RECOGNITION_MODES,
        seal_orientation_modes=SEAL_ORIENTATION_MODES,
        retention_days=database.get_retention_days(),
        retention_default_days=DEFAULT_RETENTION_DAYS,
    )


def ocr_backends():
    selected = default_backend()
    return jsonify({
        "default": selected,
        "backends": backend_catalog(),
        # Operational settings that change how the local models are run.  They
        # are environment-driven, so surfacing them here is how a caller can see
        # which strategy a deployment is actually using.
        "engine": {
            "id": ocr_engine(),
            "env": "PADDLE_OCR_ENGINE",
        },
        "seal_reading": describe_secondary_read(),
    })


def _settings_payload() -> dict:
    return {
        "retention_days": database.get_retention_days(),
        "default_retention_days": DEFAULT_RETENTION_DAYS,
    }


def get_settings():
    return jsonify(_settings_payload())


def _update_retention_settings():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or "retention_days" not in payload:
        return jsonify(error="请提供保留天数"), 400
    try:
        database.set_retention_days(payload["retention_days"])
        purge = _purge_expired_data()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify({**_settings_payload(), "deleted_results": purge.get("deleted_results", 0),
                    "deleted_jobs": purge.get("deleted_jobs", 0),
                    "deleted_tasks": purge.get("deleted_tasks", 0),
                    "files": purge.get("files", {})})


def update_settings():
    return _update_retention_settings()


def retention_settings():
    if request.method == "GET":
        return jsonify(_settings_payload())
    return _update_retention_settings()


def get_recognition_settings():
    settings = database.get_json_setting("recognition_defaults", DEFAULT_RECOGNITION_SETTINGS)
    try:
        return jsonify(_normalize_recognition_settings(settings))
    except ValueError:
        # Repair a malformed setting left by an interrupted/manual edit.
        database.set_json_setting("recognition_defaults", DEFAULT_RECOGNITION_SETTINGS)
        return jsonify(DEFAULT_RECOGNITION_SETTINGS)


def update_recognition_settings():
    try:
        settings = _normalize_recognition_settings(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    database.set_json_setting("recognition_defaults", settings)
    return jsonify(settings)


def create_task():
    return queue_handlers.create_task(sys.modules[__name__])



def list_tasks():
    return queue_handlers.list_tasks(sys.modules[__name__])



def delete_results():
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids')
    if not isinstance(ids, list) or not ids or len(ids) > 2000 or any(type(i) is not int or i <= 0 for i in ids):
        return jsonify(error='请选择有效的回单记录'), 400
    try:
        affected = database.delete_results(ids)
    except KeyError:
        return jsonify(error='回单不存在'), 404
    return jsonify(ids=affected)


def get_task(task_id: str):
    return queue_handlers.get_task(sys.modules[__name__], task_id)



def _wake_jobs():
    return queue_handlers._wake_jobs(sys.modules[__name__])



def queue_progress():
    return queue_handlers.queue_progress(sys.modules[__name__])



def queue_control():
    return queue_handlers.queue_control(sys.modules[__name__], wake=_wake_jobs)



def upload_job(job_id):
    return queue_handlers.upload_job(sys.modules[__name__], job_id, wake=_wake_jobs)



def start_jobs():
    return queue_handlers.start_jobs(sys.modules[__name__], wake=_wake_jobs)



def retry_job(job_id):
    return queue_handlers.retry_job(sys.modules[__name__], job_id, wake=_wake_jobs)



def cancel_job(job_id):
    return queue_handlers.cancel_job(sys.modules[__name__], job_id)



def _cleanup_cancelled_upload(job):
    return queue_handlers._cleanup_cancelled_upload(sys.modules[__name__], job)



def cancel_jobs():
    return queue_handlers.cancel_jobs(sys.modules[__name__])



def process_history():
    return queue_handlers.process_history(sys.modules[__name__])



def analyze_upload():
    return recognition_handlers.analyze_upload(sys.modules[__name__])



def daily_results():
    return result_handlers.daily_results(sys.modules[__name__])



def _page_options():
    return result_handlers.page_options()



def _reviewable_results(rows):
    return result_handlers.reviewable_results(sys.modules[__name__], rows)



def _receipt_listing(*, latest_by_filename):
    return result_handlers.receipt_listing(sys.modules[__name__], latest_by_filename=latest_by_filename)



def _deferred_scope_metadata(rows, deferred_ids):
    return result_handlers.deferred_scope_metadata(rows, deferred_ids)



def _selected_result_ids(parameter="ids"):
    return result_handlers.selected_result_ids(parameter)



def list_results():
    return result_handlers.list_results(sys.modules[__name__])



def import_dates():
    return result_handlers.import_dates(sys.modules[__name__])



def list_result_history():
    return result_handlers.list_result_history(sys.modules[__name__])



def get_result(result_id: int):
    return result_handlers.get_result(sys.modules[__name__], result_id)



def _review_revision(result):
    return result_handlers.review_revision(result)



def _with_review_revision(result):
    return result_handlers.with_review_revision(result)



def review_result(result_id: int):
    return review_handlers.review_result(sys.modules[__name__], result_id)



def review_history(result_id: int):
    return result_handlers.review_history(sys.modules[__name__], result_id)



def result_ground_truth(result_id: int):
    return result_handlers.result_ground_truth(sys.modules[__name__], result_id)



def retry_result(result_id: int):
    payload = request.get_json(silent=True) or {}
    if payload.get('background'):
        return _enqueue_result_retries([result_id], payload)
    try:
        current = database.get_result(result_id)
    except KeyError:
        abort(404)
    source = _source_for_record(current)
    if not source or not source.is_file():
        return jsonify({"error": "原始图片不存在，无法重新识别"}), 409
    token = uuid.uuid4().hex
    preview_name = f"{token}.jpg"
    payload = request.get_json(silent=True) or {}
    try:
        options = resolve_recognition_options(
            recognition_config=payload.get("recognition_config", current.get("recognition_config")),
            ocr_backend=payload.get("ocr_backend") or current.get("ocr_backend"),
            seal_recognition_mode=(
                payload.get("seal_recognition_mode")
                or current.get("seal_recognition_mode")
                or (current.get("seal_check") or {}).get("recognition_mode")
            ),
            api_enabled=analyzer.seal_api.enabled,
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    result = _configured_analyze(
        source,
        PREVIEW_DIR / preview_name,
        artifact_dir=ARTIFACT_DIR / token,
        artifact_url_prefix=f"/files/artifacts/{token}",
        recognition_config=options.recognition_config,
        previous_fields=recognition_fields(current),
        filename=current["filename"],
        ocr_backend=options.ocr_backend,
        seal_recognition_mode=options.seal_recognition_mode,
    )
    result.update(
        filename=current["filename"], preview_url=f"/files/previews/{preview_name}",
        created_at=current["created_at"], updated_at=now_iso(),
    )
    return jsonify(database.replace_after_retry(result_id, result, preview_name))


def bulk_review():
    return review_handlers.bulk_review(sys.modules[__name__])



def bulk_retry():
    payload = request.get_json(silent=True) or {}
    if payload.get('background'):
        return _enqueue_result_retries(payload.get('ids'), payload)
    output = []
    for raw_id in payload.get("ids", []):
        result_id = int(raw_id)
        try:
            current = database.get_result(result_id)
            source = _source_for_record(current)
            if not source or not source.is_file():
                raise FileNotFoundError("原始图片不存在")
            token = uuid.uuid4().hex
            preview_name = f"{token}.jpg"
            options = resolve_recognition_options(
                recognition_config=payload.get("recognition_config", current.get("recognition_config")),
                ocr_backend=payload.get("ocr_backend") or current.get("ocr_backend"),
                seal_recognition_mode=(
                    payload.get("seal_recognition_mode")
                    or current.get("seal_recognition_mode")
                    or (current.get("seal_check") or {}).get("recognition_mode")
                ),
                api_enabled=analyzer.seal_api.enabled,
            )
            result = _configured_analyze(
                source, PREVIEW_DIR / preview_name,
                artifact_dir=ARTIFACT_DIR / token,
                artifact_url_prefix=f"/files/artifacts/{token}",
                recognition_config=options.recognition_config,
                previous_fields=recognition_fields(current),
                filename=current["filename"],
                ocr_backend=options.ocr_backend,
                seal_recognition_mode=options.seal_recognition_mode,
            )
            result.update(filename=current["filename"], preview_url=f"/files/previews/{preview_name}")
            output.append({"id": result_id, "ok": True, "result": database.replace_after_retry(result_id, result, preview_name)})
        except Exception as exc:
            output.append({"id": result_id, "ok": False, "error": str(exc)})
    return jsonify(output)


def _enqueue_result_retries(ids, payload):
    if (not isinstance(ids, list) or not ids or len(ids) > 2000 or
        any(type(i) is not int or i <= 0 for i in ids)):
        return jsonify(error='请选择有效的回单记录'), 400
    ids = list(dict.fromkeys(ids))
    options = {}
    try:
        for result_id in ids:
            current = database.get_result(result_id)
            source = _source_for_record(current)
            if source is None or not source.is_file():
                return jsonify(error=f"原始图片不存在：{current['filename']}"), 409
            resolved = resolve_recognition_options(
                recognition_config=payload.get('recognition_config', current.get('recognition_config')),
                ocr_backend=payload.get('ocr_backend') or current.get('ocr_backend'),
                seal_recognition_mode=(
                    payload.get('seal_recognition_mode')
                    or current.get('seal_recognition_mode')
                    or 'local'
                ),
                api_enabled=analyzer.seal_api.enabled,
            )
            options[result_id] = {
                **resolved.as_dict(),
                '_stored_name': f'sample:{source.name}' if source.parent.resolve() == DATA_DIR.resolve() else current['stored_name'],
            }
        task = job_store.create_retries(uuid.uuid4().hex, ids, options)
    except KeyError:
        return jsonify(error='选中的回单不存在'), 404
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 409
    _wake_jobs()
    return jsonify(task), 202


def report():
    truth = load_ground_truth(GROUND_TRUTH_PATH)
    all_machine_results = database.list_original_results(
        limit=2000, completed_tasks_only=True
    )
    requested_backend = str(request.args.get("ocr_backend", "")).strip()
    if not requested_backend:
        try:
            requested_backend = default_backend()
        except RuntimeError:
            requested_backend = ""
    machine_results = [
        item for item in all_machine_results
        if not requested_backend or item.get("ocr_backend") == requested_backend
    ]
    if requested_backend and not machine_results:
        machine_results = all_machine_results
        requested_backend = ""
    results = database.query_receipts(
        filters={"ocr_backend": requested_backend}, latest_by_filename=True,
    )["items"]
    accuracy = evaluate_results(
        machine_results, truth
    )
    accuracy["scope_backend"] = requested_backend
    accuracy["scope_backend_label"] = (
        backend_label(requested_backend) if requested_backend else "全部后端最新结果"
    )
    report_data = operational_report(results, database.all_history(), accuracy)
    report_data["backend_comparison"] = evaluate_backends(all_machine_results, truth)
    dataset_images = sum(
        1 for path in DATA_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS
    )
    report_data["dataset"] = {
        "images": dataset_images,
        "labeled": len(truth),
        "unlabeled": max(0, dataset_images - len(truth)),
        "coverage": round(len(truth) / dataset_images, 4) if dataset_images else 0.0,
    }
    return jsonify(report_data)


def selected_seal_preview(result_id: int):
    try:
        item = _project_paginated_result(database.get_result(result_id))
        revision = request.args.get("revision")
        if revision and revision != _review_revision(item):
            abort(409)
        check = item.get("seal_check") or {}
        physical = item
        source_page = check.get("source_page")
        if source_page and source_page != item.get("filename"):
            group = item.get("page_group") or {}
            footer_id = group.get("footer_result_id")
            if footer_id not in (group.get("continuation_result_ids") or []):
                abort(404)
            physical = database.get_result(footer_id)
            if physical.get("filename") != source_page:
                abort(404)
        source = _source_for_record(physical)
        if source is None:
            abort(404)
        source = source.resolve()
        if not any(source.is_relative_to(root.resolve()) for root in (UPLOAD_DIR, DATA_DIR)):
            abort(404)
        preview = render_selected_seal(source, check)
    except (KeyError, OSError, ValueError):
        abort(404)
    response = send_file(preview, mimetype="image/png", max_age=0)
    response.cache_control.no_store = True
    return response


def files(kind: str, name: str):
    directory = {"previews": PREVIEW_DIR, "uploads": UPLOAD_DIR, "artifacts": ARTIFACT_DIR}.get(kind)
    if directory is None:
        abort(404)
    return send_from_directory(directory, name)


def _project_paginated_result(item: dict) -> dict:
    """Return one logical receipt when ``item`` is a paginated cover."""
    # Backfill the display-only signature comparison for records created
    # before the explicit signature evidence field was introduced.
    item["signature_check"] = derive_signature_check(
        item.get("fields") or {}, item.get("signature_check")
    )
    page_group = item.get("page_group") or {}
    if page_group.get("page_count", 0) <= 1 or item.get("page_role") == "continuation":
        return item
    task_id = str(item.get("task_id") or "")
    if not task_id:
        return item
    projected = database.query_receipts(filters={"task_id": task_id})["items"]
    return next((row for row in projected if int(row.get("id") or 0) == int(item.get("id") or 0)), item)


def _source_for_record(item: dict) -> Path | None:
    return ReceiptFileStore(DATA_DIR, UPLOAD_DIR).source_for_record(item)



from .routes import register_routes

register_routes(app, sys.modules[__name__])

def run() -> None:
    """Start the local Flask server."""
    # Werkzeug's reloader parent must not keep an old queue worker alive while
    # serving children restart with new code.
    if os.getenv("FLASK_DEBUG", "0") == "1" and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        app.config["BACKGROUND_WORKER"] = False
    initialize()
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5002")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )


if __name__ == "__main__":
    run()
