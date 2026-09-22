from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from receipt_ocr.analyzer import ReceiptAnalyzer
from receipt_ocr.pipeline import complete_result
from receipt_ocr.review import (
    apply_human_edits as _apply_human_edits,
    confirmation_error as _confirmation_error,
    prepare_review_payload,
)
from receipt_ocr.recognition_config import validate_config, run_configured
from receipt_ocr.field_schema import OUTPUT_FIELDS, PRINTED_FIELDS, HANDWRITTEN_FIELDS, derive_signature_check, recognition_fields
from receipt_ocr.database import DEFAULT_RETENTION_DAYS, Database, now_iso
from receipt_ocr.job_store import JobStore
from receipt_ocr.job_worker import JobWorker
from receipt_ocr.job_service import ReceiptJobService
from receipt_ocr.evaluation import (
    build_ground_truth_entry,
    evaluate_backends,
    evaluate_results,
    load_ground_truth,
    operational_report,
    save_ground_truth_entry,
)
from receipt_ocr.ocr_backends import backend_catalog, backend_label, default_backend, resolve_backend
from receipt_ocr.paddle_ocr import paddle_engine
from receipt_ocr.seal_audit_policy import describe_seal_audit
from receipt_ocr.seal_reference import SealReferenceMatcher
from receipt_ocr.qingtong_preview import render_selected_seal
from receipt_ocr.seal_api import (
    SEAL_RECOGNITION_MODES,
    resolve_seal_recognition_mode,
)
from receipt_ocr.seal_orientation import SEAL_ORIENTATION_MODES


BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = RESOURCE_DIR / "数据"
if getattr(sys, "frozen", False):
    default_storage = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SamsungReceipt"
    STORAGE_DIR = Path(os.getenv("SAMSUNG_RECEIPT_DATA_DIR", default_storage)).expanduser().resolve()
else:
    STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
PREVIEW_DIR = STORAGE_DIR / "previews"
ARTIFACT_DIR = STORAGE_DIR / "artifacts"
EXPORT_DIR = STORAGE_DIR / "exports"
DATABASE_PATH = STORAGE_DIR / "results.db"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.json"
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
analyzer = ReceiptAnalyzer()
database = Database(DATABASE_PATH)
seal_reference_matcher = SealReferenceMatcher(ARTIFACT_DIR)
_initialized = False
_initialization_lock = threading.Lock()
_retention_cleanup_lock = threading.Lock()
_last_retention_cleanup = 0.0
job_store = JobStore(database)
job_worker = None


def _storage_references() -> tuple[set[str], set[str], set[str]]:
    """Return upload, preview and artifact names still referenced by rows."""
    uploads, previews, artifacts = set(), set(), set()
    with database.connect() as connection:
        for row in connection.execute(
            "SELECT stored_name,preview_name,result_json FROM results WHERE deleted_at=''"
        ):
            stored_name = str(row["stored_name"] or "")
            if stored_name and not stored_name.startswith("sample:"):
                uploads.add(Path(stored_name).name)
            preview_name = str(row["preview_name"] or "")
            if preview_name:
                previews.add(Path(preview_name).name)
            artifacts.update(re.findall(
                r"/files/artifacts/([A-Za-z0-9_-]+)(?:/|\b)", row["result_json"] or ""
            ))
        has_jobs = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_jobs'"
        ).fetchone()
        if has_jobs:
            for row in connection.execute("SELECT stored_name FROM recognition_jobs WHERE stored_name<>''"):
                stored_name = str(row["stored_name"] or "")
                if not stored_name.startswith("sample:"):
                    uploads.add(Path(stored_name).name)
    return uploads, previews, artifacts


def _cleanup_expired_storage(purge: dict) -> dict[str, int]:
    """Delete files no longer referenced after retention cleanup."""
    try:
        cutoff_timestamp = datetime.fromisoformat(str(purge.get("cutoff"))).timestamp()
    except (TypeError, ValueError, OverflowError):
        cutoff_timestamp = None
    referenced_uploads, referenced_previews, referenced_artifacts = _storage_references()
    removed = {"uploads": 0, "previews": 0, "artifacts": 0, "exports": 0}
    artifact_tokens = set()
    for row in purge.get("records") or []:
        stored_name = str(row.get("stored_name") or "")
        if stored_name and not stored_name.startswith("sample:"):
            candidate = (UPLOAD_DIR / Path(stored_name).name).resolve()
            if candidate.parent == UPLOAD_DIR.resolve() and candidate.name not in referenced_uploads and candidate.is_file():
                candidate.unlink(missing_ok=True)
                removed["uploads"] += 1
        preview_name = str(row.get("preview_name") or "")
        if preview_name:
            candidate = (PREVIEW_DIR / Path(preview_name).name).resolve()
            if candidate.parent == PREVIEW_DIR.resolve() and candidate.name not in referenced_previews and candidate.is_file():
                candidate.unlink(missing_ok=True)
                removed["previews"] += 1
        artifact_tokens.update(re.findall(
            r"/files/artifacts/([A-Za-z0-9_-]+)(?:/|\b)", row.get("result_json") or ""
        ))
    for token in artifact_tokens - referenced_artifacts:
        candidate = (ARTIFACT_DIR / token).resolve()
        if candidate.parent == ARTIFACT_DIR.resolve() and candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
            removed["artifacts"] += 1
    if cutoff_timestamp is None:
        return removed

    def is_old(path: Path) -> bool:
        try:
            return path.stat().st_mtime < cutoff_timestamp
        except OSError:
            return False

    for directory, references, key in ((UPLOAD_DIR, referenced_uploads, "uploads"),
                                       (PREVIEW_DIR, referenced_previews, "previews")):
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and path.name not in references and is_old(path):
                    path.unlink(missing_ok=True)
                    removed[key] += 1
    if ARTIFACT_DIR.is_dir():
        for path in ARTIFACT_DIR.iterdir():
            if path.is_dir() and path.name not in referenced_artifacts and is_old(path):
                shutil.rmtree(path, ignore_errors=True)
                removed["artifacts"] += 1
    if EXPORT_DIR.is_dir():
        for path in EXPORT_DIR.iterdir():
            if path.is_file() and is_old(path):
                path.unlink(missing_ok=True)
                removed["exports"] += 1
    return removed


def _purge_expired_data() -> dict:
    try:
        purge = database.purge_expired()
        purge["files"] = _cleanup_expired_storage(purge)
        if purge.get("deleted_results") or any(purge["files"].values()):
            app.logger.info(
                "已按保留天数清理数据：记录 %s，任务 %s，文件 %s",
                purge.get("deleted_results", 0), purge.get("deleted_tasks", 0), purge["files"],
            )
        return purge
    except Exception:
        app.logger.exception("按保留天数清理数据失败")
        return {"retention_days": database.get_retention_days(), "deleted_results": 0,
                "deleted_jobs": 0, "deleted_tasks": 0, "files": {}}


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


@app.before_request
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
    kwargs['reference_matcher'] = seal_reference_matcher
    if recognition_config is None:
        return analyzer.analyze(*args, **kwargs)
    return run_configured(analyzer, *args, config=recognition_config, previous_fields=previous_fields, **kwargs)


def _apply_visual_seal_reference(result: dict) -> dict:
    # Compatibility for saved-result audit tools. Live recognition finalizes
    # inside the pipeline, with the original filename supplied before matching.
    return complete_result(result, reference_matcher=seal_reference_matcher)


@app.get("/")
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


@app.get("/api/ocr-backends")
def ocr_backends():
    selected = default_backend()
    return jsonify({
        "default": selected,
        "backends": backend_catalog(),
        # Operational settings that change how the local models are run.  They
        # are environment-driven, so surfacing them here is how a caller can see
        # which strategy a deployment is actually using.
        "engine": {
            "id": paddle_engine(),
            "env": "PADDLE_OCR_ENGINE",
        },
        "seal_audit": describe_seal_audit(),
    })


def _settings_payload() -> dict:
    return {
        "retention_days": database.get_retention_days(),
        "default_retention_days": DEFAULT_RETENTION_DAYS,
    }


@app.get("/api/settings")
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


@app.patch("/api/settings")
def update_settings():
    return _update_retention_settings()


@app.route("/api/settings/retention", methods=["GET", "PATCH"])
@app.route("/api/settings/retention-days", methods=["GET", "PATCH"])
@app.route("/api/settings/data-retention", methods=["GET", "PATCH"])
def retention_settings():
    if request.method == "GET":
        return jsonify(_settings_payload())
    return _update_retention_settings()


@app.post("/api/tasks")
def create_task():
    payload = request.get_json(silent=True) or {}
    total = payload.get("total", 0)
    if type(total) is not int:
        return jsonify(error='批量任务数量必须是整数'), 400
    if total <= 0 or total > 5000:
        return jsonify({"error": "批量任务数量必须在 1 至 5000 之间"}), 400
    try:
        recognition_config = validate_config(payload.get("recognition_config"), api_enabled=analyzer.seal_api.enabled)
        ocr_backend = resolve_backend(str(payload.get("ocr_backend") or "auto"))
        seal_recognition_mode = resolve_seal_recognition_mode(
            payload.get("seal_recognition_mode")
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    task_id = uuid.uuid4().hex
    if payload.get('background'):
        items = payload.get('items')
        if (not isinstance(items, list) or len(items) != total or
            any(not isinstance(item, dict) or not isinstance(item.get('filename'), str)
                or not item['filename'].strip() or len(item['filename']) > 500 for item in items)):
            return jsonify(error='请提供与上传数量一致的图片清单'), 400
        return jsonify(job_store.create_batch(task_id, str(payload.get('name') or '批量识别'), items,
            dict(ocr_backend=ocr_backend, seal_recognition_mode=seal_recognition_mode,
                 recognition_config=recognition_config))), 201
    return jsonify(database.create_task(
        task_id, str(payload.get("name") or "批量识别"), total, ocr_backend,
        seal_recognition_mode, recognition_config,
    ))


@app.get("/api/tasks")
def list_tasks():
    return jsonify(database.list_tasks())


@app.post("/api/results/delete")
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


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    try:
        task = database.get_task(task_id)
        return jsonify(job_store.task(task_id) if task['execution_mode'] == 'queue' else task)
    except KeyError:
        abort(404)


def _wake_jobs():
    if job_worker is not None:
        job_worker.start()


@app.get('/api/queue')
def queue_progress():
    from datetime import date
    day = request.args.get('import_date', '')
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        return jsonify(error='请选择有效的导入日期'), 400
    return jsonify(job_store.daily(day))


@app.route('/api/queue/control', methods=['GET', 'POST'])
def queue_control():
    if request.method == 'GET':
        return jsonify(job_store.control())
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get('paused'), bool):
        return jsonify(error='请指定是否暂停识别'), 400
    control = job_store.set_paused(payload['paused'])
    if not control['paused']:
        _wake_jobs()
    return jsonify(control)


@app.post('/api/jobs/<job_id>/upload')
def upload_job(job_id):
    try:
        job = job_store.get(job_id)
    except KeyError:
        abort(404)
    # A lost HTTP response can be retried without creating another result.
    if job['status'] != 'awaiting_upload':
        return jsonify(job_store.public(job)), 200
    upload = request.files.get('file')
    sample = request.form.get('sample', '')
    saved_path = None
    if upload and upload.filename:
        expected = Path(job['filename'].replace('\\', '/')).name
        if Path(upload.filename.replace('\\', '/')).name != expected:
            return jsonify(error=f'请选择原任务中的图片：{expected}'), 400
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            return jsonify(error='仅支持 JPG、PNG、BMP、WEBP 图片'), 400
        stored_name = f'{uuid.uuid4().hex}{suffix}'
        saved_path = UPLOAD_DIR / stored_name
        temporary = UPLOAD_DIR / f'{stored_name}.part'
        try:
            upload.save(temporary)
            if temporary.stat().st_size == 0:
                return jsonify(error='图片为空，请重新选择'), 400
            temporary.replace(saved_path)
        finally:
            temporary.unlink(missing_ok=True)
    elif sample:
        source = (DATA_DIR / Path(sample).name).resolve()
        if source.parent != DATA_DIR.resolve() or not source.is_file() or source.suffix.lower() not in ALLOWED_EXTENSIONS:
            abort(404)
        if source.name != Path(job['filename'].replace('\\', '/')).name:
            return jsonify(error='样单与任务清单不一致'), 400
        stored_name = f'sample:{source.name}'
    else:
        return jsonify(error='请选择图片'), 400
    try:
        job, accepted = job_store.accept_upload(job_id, stored_name)
    except Exception:
        if saved_path:
            saved_path.unlink(missing_ok=True)
        raise
    if not accepted and saved_path:
        saved_path.unlink(missing_ok=True)
    if job['start_requested']:
        _wake_jobs()
    return jsonify(job_store.public(job)), 202


@app.post('/api/jobs/start')
def start_jobs():
    payload = request.get_json(silent=True)
    ids = payload.get('ids') if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not ids or len(ids) > 5000
            or any(not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 100 for job_id in ids)):
        return jsonify(error='请选择有效的待开始回单'), 400
    try:
        started = job_store.start_jobs(ids)
    except KeyError:
        return jsonify(error='所选回单不存在'), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    _wake_jobs()
    return jsonify(**started, control=job_store.control()), 202


@app.post('/api/jobs/<job_id>/retry')
def retry_job(job_id):
    try:
        job = job_store.retry_failed(job_id)
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    _wake_jobs()
    return jsonify(job_store.public(job)), 202


@app.post('/api/jobs/<job_id>/cancel')
def cancel_job(job_id):
    try:
        job, deleted = job_store.delete_job(job_id)
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    if not deleted:
        return jsonify(error='这条回单已经完成，不能取消'), 409
    _cleanup_cancelled_upload(job)
    return jsonify(deleted=True, job_id=job_id), 200


def _cleanup_cancelled_upload(job):
    stored_name = job.get('stored_name') or ''
    if stored_name and not job.get('preserve_upload') and not stored_name.startswith('sample:'):
        candidate = (UPLOAD_DIR / Path(stored_name).name).resolve()
        if candidate.parent == UPLOAD_DIR.resolve():
            candidate.unlink(missing_ok=True)


@app.post('/api/jobs/cancel')
def cancel_jobs():
    payload = request.get_json(silent=True) or {}
    ids = payload.get('ids') if isinstance(payload, dict) else None
    try:
        result = job_store.delete_jobs(ids)
    except KeyError:
        return jsonify(error='所选待处理回单中有任务已不存在，请刷新后重试'), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    for job in result['deleted']:
        _cleanup_cancelled_upload(job)
    return jsonify(deleted=True, deleted_ids=[job['id'] for job in result['deleted']],
                   kept_ids=[job['id'] for job in result['kept']]), 200


@app.get('/api/process-history')
def process_history():
    job_id = request.args.get('job_id', '').strip()
    result_id_raw = request.args.get('result_id', '').strip()
    result_id = 0
    if result_id_raw:
        try:
            result_id = int(result_id_raw)
        except ValueError:
            return jsonify(error='请选择有效的回单记录'), 400
    if not job_id and not result_id:
        return jsonify(error='请选择需要查看流程的回单'), 400
    try:
        return jsonify(job_store.process_history(job_id=job_id, result_id=result_id))
    except KeyError:
        abort(404)


@app.post("/api/analyze")
def analyze_upload():
    task_id = request.form.get("task_id", "").strip()
    if task_id:
        try:
            if database.get_task(task_id)['execution_mode'] == 'queue':
                return jsonify(error='该任务请通过后台上传接口提交图片'), 409
        except KeyError:
            return jsonify(error='批量任务不存在'), 404
    upload = request.files.get("file")
    sample_name = request.form.get("sample", "").strip()
    source = None
    original_name = ""
    stored_name = ""
    if upload and upload.filename:
        original_name = Path(upload.filename).name
        safe_name = secure_filename(original_name) or f"receipt{Path(original_name).suffix}"
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            return jsonify({"error": "仅支持 JPG、PNG、BMP、WEBP 图片"}), 400
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        source = UPLOAD_DIR / stored_name
        upload.save(source)
    elif sample_name:
        source = (DATA_DIR / Path(sample_name).name).resolve()
        if source.parent != DATA_DIR.resolve() or not source.is_file():
            abort(404)
        original_name = source.name
        stored_name = f"sample:{source.name}"
    else:
        return jsonify({"error": "请选择图片"}), 400

    if task_id:
        try:
            task = database.get_task(task_id)
            recognition_config = validate_config(json.loads(task.get("recognition_config") or "null"), api_enabled=analyzer.seal_api.enabled)
            ocr_backend = resolve_backend(task.get("ocr_backend") or "auto")
            seal_recognition_mode = resolve_seal_recognition_mode(
                task.get("seal_recognition_mode")
            )
        except KeyError:
            return jsonify({"error": "批量任务不存在"}), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
    else:
        try:
            recognition_config = validate_config(json.loads(request.form.get("recognition_config", "null")), api_enabled=analyzer.seal_api.enabled)
            ocr_backend = resolve_backend(request.form.get("ocr_backend", "auto"))
            seal_recognition_mode = resolve_seal_recognition_mode(
                request.form.get("seal_recognition_mode")
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        task_id = uuid.uuid4().hex
        database.create_task(
            task_id, original_name, 1, ocr_backend, seal_recognition_mode, recognition_config
        )

    token = uuid.uuid4().hex
    preview_name = f"{token}.jpg"
    artifacts = ARTIFACT_DIR / token
    try:
        result = _configured_analyze(
            source,
            PREVIEW_DIR / preview_name,
            artifact_dir=artifacts,
            artifact_url_prefix=f"/files/artifacts/{token}",
            recognition_config=recognition_config,
            filename=original_name,
            ocr_backend=ocr_backend,
            seal_recognition_mode=seal_recognition_mode,
        )
        result.update(
            filename=original_name,
            preview_url=f"/files/previews/{preview_name}",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        record_id = database.insert_result(
            filename=original_name,
            stored_name=stored_name,
            preview_name=preview_name,
            task_id=task_id,
            result=result,
        )
        result["id"] = record_id
        database.update_task(task_id, success=True, pending_review=result["review_status"] == "待复核")
        return jsonify(result)
    except Exception as exc:
        app.logger.exception("Receipt analysis failed")
        failure = {
            "filename": original_name,
            "overall": "识别失败",
            "final_result": "识别失败",
            "review_status": "待复核",
            "review_reasons": ["识别流程异常"],
            "ocr_backend": ocr_backend,
            "ocr_backend_label": backend_label(ocr_backend),
            "seal_recognition_mode": seal_recognition_mode,
            "fields": {},
            "field_metadata": {},
            "date_check": {"required": "", "actual": "", "status": "未识别", "confidence": 0},
            "seal_check": {"requirement": "", "recognized": "", "status": "未识别", "score": 0},
            "processing_artifacts": {"date": [], "seals": []},
            "processing_seconds": 0,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        record_id = database.insert_result(
            filename=original_name,
            stored_name=stored_name,
            preview_name="",
            task_id=task_id,
            result=failure,
            error_type="识别失败",
            error_message=str(exc),
        )
        database.update_task(task_id, success=False, pending_review=True)
        return jsonify({"error": f"识别失败：{exc}", "id": record_id}), 500


@app.get("/api/daily-results")
def daily_results():
    from datetime import date
    day = request.args.get("import_date", "")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        return jsonify(error="请选择有效的导入日期"), 400
    try:
        deferred_ids = _selected_result_ids('deferred_ids')
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    rows = database.query_receipts(filters={"import_date": day}, latest_by_filename=True)["items"]
    if request.args.get('include_queue') == '1':
        # Retries belong to their task day, while the result keeps its original
        # import date. Resolve missing results by ID, never by filename.
        seen = {row['id'] for row in rows}
        for job in job_store.daily(day):
            result_id = job.get('result_id') or job.get('target_result_id')
            if not result_id or result_id in seen:
                continue
            try:
                projected = _project_paginated_result(database.get_result(result_id))
                if projected['id'] not in seen:
                    rows.append(projected)
                    seen.add(projected['id'])
            except KeyError:
                continue  # A result may be deleted between these reads.
    if request.args.get('reviewable') == '1':
        rows = _reviewable_results(rows)
    rows.sort(key=lambda row: row['id'], reverse=True)
    if 'page' in request.args or 'page_size' in request.args:
        try:
            page, page_size = _page_options()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(items=rows[(page - 1) * page_size:page * page_size],
                       total=len(rows), page=page, page_size=page_size,
                       **_deferred_scope_metadata(rows, deferred_ids))
    return jsonify(rows)


def _page_options():
    try:
        page = int(request.args.get('page', '1'))
        page_size = int(request.args.get('page_size', '100'))
    except ValueError:
        raise ValueError('页码和每页数量必须为正整数') from None
    if page < 1 or not 1 <= page_size <= 2000:
        raise ValueError('页码必须大于零，每页数量必须在 1 至 2000 之间')
    return page, page_size


def _reviewable_results(rows):
    """Filter the logical receipt before pagination, including busy linked pages."""
    with database.connect() as connection:
        has_jobs = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_jobs'").fetchone()
        busy = set()
        if has_jobs:
            for job in connection.execute("SELECT result_id,target_result_id FROM recognition_jobs WHERE status IN ('awaiting_upload','queued','running')"):
                busy.update(value for value in job if value)
    return [row for row in rows if row.get('review_status') == '待复核'
            and not row.get('error_message') and row.get('overall') != '识别失败'
            and not busy.intersection({row['id'], *(row.get('page_group') or {}).get('continuation_result_ids', [])})]


def _receipt_listing(*, latest_by_filename):
    filters = {key: request.args.get(key, '') for key in RESULT_FILTERS}
    paged = 'page' in request.args or 'page_size' in request.args
    try:
        page, page_size = _page_options() if paged else (1, min(max(request.args.get('limit', 200, type=int), 1), 2000))
        selected_ids = _selected_result_ids()
        deferred_ids = _selected_result_ids('deferred_ids')
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    offset = (page - 1) * page_size
    if request.args.get('reviewable') == '1' or selected_ids is not None or deferred_ids is not None:
        rows = database.query_receipts(filters=filters, latest_by_filename=latest_by_filename)['items']
        if selected_ids is not None:
            rows = [row for row in rows if selected_ids.intersection({row['id'],
                    *(row.get('page_group') or {}).get('continuation_result_ids', [])})]
        if request.args.get('reviewable') == '1':
            rows = _reviewable_results(rows)
        result = {'items': rows[offset:offset + page_size], 'total': len(rows),
                  **_deferred_scope_metadata(rows, deferred_ids)}
    else:
        result = database.query_receipts(filters=filters, latest_by_filename=latest_by_filename,
                                         limit=page_size, offset=offset)
    return jsonify({**result, 'page': page, 'page_size': page_size} if paged else result['items'])


def _deferred_scope_metadata(rows, deferred_ids):
    if deferred_ids is None:
        return {}
    return {'deferred_in_scope_ids': [row['id'] for row in rows
            if deferred_ids.intersection({row['id'],
                *(row.get('page_group') or {}).get('continuation_result_ids', [])})]}


def _selected_result_ids(parameter='ids'):
    """An explicit empty selection stays empty; malformed scopes never broaden."""
    if parameter not in request.args:
        return None
    values = request.args.getlist(parameter)
    if len(values) != 1:
        raise ValueError('选中的回单编号格式无效')
    if values[0] == '':
        return set()
    ids = set()
    for value in values[0].split(','):
        if not value.isascii() or not value.isdigit() or value.startswith('0') or len(value) > 19:
            raise ValueError('选中的回单编号必须为正整数')
        result_id = int(value)
        if result_id > 9223372036854775807:
            raise ValueError('选中的回单编号超出有效范围')
        ids.add(result_id)
    return ids


@app.get("/api/results")
def list_results():
    return _receipt_listing(latest_by_filename=True)


@app.get("/api/import-dates")
def import_dates():
    month = request.args.get("month", "")
    try:
        from datetime import datetime
        parsed = datetime.strptime(month, "%Y-%m")
        if parsed.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        return jsonify({"error": "请选择有效月份"}), 400
    return jsonify(database.import_date_counts(month, request.args.get("ocr_backend", "")))


@app.get("/api/history")
def list_result_history():
    return _receipt_listing(latest_by_filename=False)


@app.get("/api/results/<int:result_id>")
def get_result(result_id: int):
    try:
        return jsonify(_with_review_revision(_project_paginated_result(database.get_result(result_id))))
    except KeyError:
        abort(404)


def _review_revision(result):
    evidence = {key: value for key, value in result.items() if key not in {'review_revision', 'ground_truth_saved'}}
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _with_review_revision(result):
    return {**result, 'review_revision': _review_revision(result)}


@app.patch("/api/results/<int:result_id>/review")
def review_result(result_id: int):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="请提供有效的复核内容"), 400
    if payload.get('actual_date_confirmed') is not None and not isinstance(payload['actual_date_confirmed'], bool):
        return jsonify(error="日期确认结果必须为布尔值"), 400
    if payload.get('signature_confirmed_match') is not None and not isinstance(payload['signature_confirmed_match'], bool):
        return jsonify(error="签名确认结果必须为匹配或不匹配"), 400
    seal_confirmation = payload.get('seal_confirmed_match')
    if seal_confirmation is not None and not isinstance(seal_confirmation, bool):
        return jsonify(error="印章确认结果必须为匹配或不匹配"), 400
    if payload.get('save_ground_truth') and isinstance(seal_confirmation, bool) and payload.get('truth_seal_should_match') is not seal_confirmation:
        return jsonify(error="印章真值结论与本次人工确认不一致，请核对"), 409
    try:
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            stored_current = database.get_result(result_id)
            current = _project_paginated_result(stored_current)
            if 'review_revision' in payload and payload['review_revision'] != _review_revision(current):
                return jsonify(error='这张回单已在其他窗口或重新识别中更新，请重新打开后核对；当前编辑尚未保存。',
                               code='review_revision_conflict'), 409
            updated = _apply_human_edits(current, payload)
            review_status = str(payload.get("review_status", "待复核"))
            final_result = str(payload.get("final_result") or updated.get("overall", "需人工复核"))
            confirmation_error = _confirmation_error(updated, review_status, final_result)
            if confirmation_error:
                return jsonify(error=confirmation_error), 409
            truth_entry = None
            if payload.get("save_ground_truth"):
                if review_status not in {"确认通过", "确认不通过"}:
                    return jsonify(error="只有完成确认通过/不通过后才能保存评测真值"), 400
                seal_should_match = payload.get("truth_seal_should_match")
                if not isinstance(seal_should_match, bool):
                    return jsonify(error="请选择真值中的印章是否应匹配"), 400
                truth_entry = build_ground_truth_entry(updated, seal_should_match=seal_should_match,
                    date_present=bool(payload.get("truth_date_present", True)))
            result_to_store = prepare_review_payload(stored_current, updated,
                review_status=review_status, final_result=final_result,
                note=str(payload.get("human_note", "")), error_type=str(payload.get("error_type", "")))
            database.review_result(result_id, result=result_to_store, review_status=review_status,
                final_result=final_result, note=str(payload.get("human_note", "")),
                action=str(payload.get("action", "人工复核")), error_type=str(payload.get("error_type", "")),
                _connection=connection)
    except KeyError:
        abort(404)
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, ValueError) else '复核字段格式无效，请检查输入内容'), 400
    reviewed = _project_paginated_result(database.get_result(result_id))
    if truth_entry is not None:
        change = save_ground_truth_entry(
            GROUND_TRUTH_PATH, reviewed["filename"], truth_entry
        )
        history = database.record_ground_truth_change(
            filename=reviewed["filename"], result_id=result_id,
            before=change["before"], after=change["after"],
            note=str(payload.get("human_note", "")),
        )
        reviewed["ground_truth_saved"] = {
            "total": change["total"], "action": history["action"],
            "changed_at": history["changed_at"],
        }
        seal_reference_matcher.refresh(
            database, load_ground_truth(GROUND_TRUTH_PATH)
        )
    return jsonify(_with_review_revision(reviewed))


@app.get("/api/results/<int:result_id>/review-history")
def review_history(result_id: int):
    return jsonify(database.history(result_id))


@app.get("/api/results/<int:result_id>/ground-truth")
def result_ground_truth(result_id: int):
    try:
        current = database.get_result(result_id)
    except KeyError:
        abort(404)
    truth = load_ground_truth(GROUND_TRUTH_PATH)
    filename = current["filename"]
    return jsonify({
        "filename": filename,
        "exists": filename in truth,
        "entry": truth.get(filename),
        "history": database.ground_truth_history(filename),
    })


@app.post("/api/results/<int:result_id>/retry")
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
        recognition_config = validate_config(payload.get("recognition_config", current.get("recognition_config")), api_enabled=analyzer.seal_api.enabled)
        ocr_backend = resolve_backend(
            str(payload.get("ocr_backend") or current.get("ocr_backend") or "auto")
        )
        seal_recognition_mode = resolve_seal_recognition_mode(
            payload.get("seal_recognition_mode")
            or current.get("seal_recognition_mode")
            or (current.get("seal_check") or {}).get("recognition_mode")
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    result = _configured_analyze(
        source,
        PREVIEW_DIR / preview_name,
        artifact_dir=ARTIFACT_DIR / token,
        artifact_url_prefix=f"/files/artifacts/{token}",
        recognition_config=recognition_config,
        previous_fields=recognition_fields(current),
        filename=current["filename"],
        ocr_backend=ocr_backend,
        seal_recognition_mode=seal_recognition_mode,
    )
    result.update(
        filename=current["filename"], preview_url=f"/files/previews/{preview_name}",
        created_at=current["created_at"], updated_at=now_iso(),
    )
    return jsonify(database.replace_after_retry(result_id, result, preview_name))


@app.post("/api/results/bulk-review")
def bulk_review():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not ids or len(ids) > 2000 or
        any(type(item) is not int or item <= 0 for item in ids)):
        return jsonify(error="请选择有效的回单记录"), 400
    ids = list(dict.fromkeys(ids))
    review_status = str(payload.get("review_status", "待复核"))
    requested_final = str(payload.get("final_result") or "")
    try:
        with database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current_items = []
            invalid = []
            for result_id in ids:
                stored = database.get_result(result_id)
                current = _project_paginated_result(stored)
                final_result = requested_final or str(current.get("overall", ""))
                error = _confirmation_error(current, review_status, final_result)
                if error:
                    invalid.append({"id": result_id, "filename": current.get("filename", ""), "reason": error})
                current_items.append((result_id, stored, current, final_result))
            if invalid:
                return jsonify(error="部分回单的日期或印章证据不完整，不能批量确认通过", invalid=invalid), 409
            for result_id, stored, current, final_result in current_items:
                note = str(payload.get("human_note", ""))
                reviewed = prepare_review_payload(stored, current, review_status=review_status,
                                                   final_result=final_result, note=note)
                database.review_result(result_id, result=reviewed, review_status=review_status,
                    final_result=final_result, note=note, action="批量确认", _connection=connection)
    except KeyError:
        return jsonify(error="选中的回单不存在"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify([_project_paginated_result(database.get_result(result_id)) for result_id in ids])


@app.post("/api/results/bulk-retry")
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
            ocr_backend = resolve_backend(
                str(payload.get("ocr_backend") or current.get("ocr_backend") or "auto")
            )
            seal_recognition_mode = resolve_seal_recognition_mode(
                payload.get("seal_recognition_mode")
                or current.get("seal_recognition_mode")
                or (current.get("seal_check") or {}).get("recognition_mode")
            )
            result = _configured_analyze(
                source, PREVIEW_DIR / preview_name,
                artifact_dir=ARTIFACT_DIR / token,
                artifact_url_prefix=f"/files/artifacts/{token}",
                recognition_config=payload.get("recognition_config", current.get("recognition_config")),
                previous_fields=recognition_fields(current),
                filename=current["filename"],
                ocr_backend=ocr_backend,
                seal_recognition_mode=seal_recognition_mode,
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
            options[result_id] = {
                'recognition_config': validate_config(payload.get('recognition_config', current.get('recognition_config')),
                    api_enabled=analyzer.seal_api.enabled),
                'ocr_backend': resolve_backend(str(payload.get('ocr_backend') or current.get('ocr_backend') or 'auto')),
                'seal_recognition_mode': resolve_seal_recognition_mode(
                    payload.get('seal_recognition_mode') or current.get('seal_recognition_mode') or 'local'),
                '_stored_name': f'sample:{source.name}' if source.parent.resolve() == DATA_DIR.resolve() else current['stored_name'],
            }
        task = job_store.create_retries(uuid.uuid4().hex, ids, options)
    except KeyError:
        return jsonify(error='选中的回单不存在'), 404
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 409
    _wake_jobs()
    return jsonify(task), 202


@app.get("/api/report")
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


@app.get("/files/selected-seal/<int:result_id>.png")
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


@app.get("/files/<kind>/<path:name>")
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
    stored = item.get("stored_name", "")
    if stored.startswith("sample:"):
        return DATA_DIR / stored.split(":", 1)[1]
    upload = UPLOAD_DIR / stored
    if upload.is_file():
        return upload
    fallback = DATA_DIR / item.get("filename", "")
    return fallback if fallback.is_file() else None


if __name__ == "__main__":
    # Werkzeug's reloader parent must not keep an old queue worker alive while
    # serving children restart with new code.
    if os.getenv('FLASK_DEBUG', '0') == '1' and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
        app.config['BACKGROUND_WORKER'] = False
    initialize()
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
