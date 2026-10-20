"""Customer-organized sample library with a separate, durable test database.

The normal recognizer is reused. Tasks, results and generated images are
stored independently from production receipt data.
"""
from collections import Counter
from pathlib import Path
import json
import threading
import uuid

from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory

from receipt_ocr.application import resolve_recognition_options
from receipt_ocr.domain.fields.schema import derive_signature_check
from receipt_ocr.domain.seal_standards import apply_standard_check
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.jobs.service import ReceiptJobService
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.jobs.worker import JobWorker
from receipt_ocr.persistence.database import Database, now_iso
from receipt_ocr.recognition.seal.providers.qingtong import render_selected_seal
from .results import review_revision, with_review_revision
from .seal_standards import SealStandardDependencies, register_standard
from .sample_statistics import sample_statistics


def sample_urls(value):
    if isinstance(value, dict):
        return {key: sample_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sample_urls(item) for item in value]
    if isinstance(value, str) and value.startswith(("/files/previews/", "/files/artifacts/")):
        return value.replace("/files/", "/files/samples/", 1)
    return value


class SampleJobStore(JobStore):
    def claim(self):
        job = super().claim()
        if job:
            payload = json.loads(job["payload_json"])
            # Sample comparisons retain the plan selected when Run was clicked,
            # even if another batch subsequently selects a different plan.
            snapshot = payload.get("sample_options")
            if snapshot:
                payload.update(snapshot)
                job["payload_json"] = json.dumps(payload, ensure_ascii=False)
                with self.database.connect() as con:
                    con.execute("UPDATE recognition_jobs SET payload_json=? WHERE id=? AND attempt=?",
                                (job["payload_json"], job["id"], job["attempt"]))
        return job

    def enqueue(self, names, options, settings):
        """Publish a whole batch atomically and exclude already active samples."""
        task_id, timestamp = uuid.uuid4().hex, now_iso()
        with self.database.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            busy = {row[0] for row in con.execute(
                "SELECT filename FROM recognition_jobs WHERE status IN ('awaiting_upload','queued','running')"
            )}
            accepted = [name for name in names if name not in busy]
            if not accepted:
                return {"accepted": [], "skipped": names, "task_id": None}
            con.execute(
                "INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)",
                ("recognition_defaults", json.dumps(settings, ensure_ascii=False), timestamp),
            )
            con.execute("""INSERT INTO batch_tasks
                (id,name,created_at,updated_at,status,total,ocr_backend,seal_recognition_mode,
                 recognition_config,execution_mode)
                VALUES(?,?,?,?,?,?,?,?,?,'queue')""",
                (task_id, f"样单测试 · {len(accepted)} 张", timestamp, timestamp, "处理中",
                 len(accepted), options["ocr_backend"], options["seal_recognition_mode"],
                 json.dumps(options.get("recognition_config"))))
            for index, name in enumerate(accepted):
                job_id = uuid.uuid4().hex
                con.execute("""INSERT INTO recognition_jobs
                    (id,task_id,client_key,filename,stored_name,status,start_requested,
                     payload_json,created_at,uploaded_at)
                    VALUES(?,?,?,?,?,'queued',1,?,?,?)""",
                    (job_id, task_id, str(index), name, f"sample:{name}",
                     json.dumps({**options, "sample_options": options}, ensure_ascii=False), timestamp, timestamp))
                self._log(con, job_id, task_id, "submitted", "开始样单测试", name,
                          "queued", event_at=timestamp)
            self._refresh(con, task_id)
        return {"accepted": accepted, "skipped": [name for name in names if name in busy],
                "task_id": task_id}


class SampleCenter:
    def __init__(self, ctx):
        self.ctx = ctx
        self.database = Database(ctx.database.path.parent / "sample-center" / "results.db")
        self.database.path.parent.mkdir(parents=True, exist_ok=True)
        self.database.initialize()
        self.store = SampleJobStore(self.database)
        self.store.initialize()
        self.preview_dir = self.database.path.parent / "previews"
        self.artifact_dir = self.database.path.parent / "artifacts"
        self.preview_dir.mkdir(exist_ok=True)
        self.artifact_dir.mkdir(exist_ok=True)
        service = ReceiptJobService(
            ctx._configured_analyze, data_dir=ctx.DATA_DIR, upload_dir=ctx.UPLOAD_DIR,
            preview_dir=self.preview_dir, artifact_dir=self.artifact_dir)

        def execute(job):
            result, preview = service(job)
            return sample_urls(result), preview

        self.worker = JobWorker(self.store, execute, logger=ctx.app.logger)

    def catalog(self):
        entries = load_ground_truth(self.ctx.GROUND_TRUTH_PATH)
        return {name: entry for name, entry in entries.items()
                if isinstance(name, str) and Path(name).name == name
                and (self.ctx.DATA_DIR / name).is_file()
                and (self.ctx.DATA_DIR / name).resolve().parent == self.ctx.DATA_DIR.resolve()
                and Path(name).suffix.lower() in self.ctx.ALLOWED_EXTENSIONS}

    def source(self, name):
        if name not in self.catalog():
            abort(404)
        source = (self.ctx.DATA_DIR / name).resolve()
        if source.parent != self.ctx.DATA_DIR.resolve():
            abort(404)
        return source

    def listing(self):
        catalog = self.catalog()
        # Only the latest job per filename is needed, not full OCR artifacts.
        with self.database.connect() as con:
            jobs = {row["filename"]: dict(row) for row in con.execute("""
                SELECT j.filename,j.status,j.result_id,j.error_message,j.created_at,j.finished_at,
                    json_extract(r.result_json,'$.fields') AS fields,
                    json_extract(r.result_json,'$.signature_check') AS signature_check,
                    json_extract(r.result_json,'$.date_check.status') AS date_status,
                    json_extract(r.result_json,'$.seal_check.status') AS seal_status
                FROM recognition_jobs j
                LEFT JOIN results r ON r.id=j.result_id AND r.deleted_at=''
                WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
            """)}
        items = []
        for name, entry in catalog.items():
            job = jobs.get(name, {})
            signature = derive_signature_check(json.loads(job.get("fields") or "{}"),
                                               json.loads(job.get("signature_check") or "{}"))
            status = {"queued": "测试中", "running": "测试中", "succeeded": "已完成",
                      "failed": "运行失败"}.get(job.get("status"), "未测试")
            items.append({
                "filename": name,
                "company": str((entry.get("fields") or {}).get("客户名称") or "").strip() or "未分类",
                "status": status, "job_status": job.get("status", ""),
                "result_id": job.get("result_id"), "error": job.get("error_message", ""),
                "updated_at": job.get("finished_at") or job.get("created_at", ""),
                "checks": {key: job.get(f"{key}_status") or "未执行"
                           for key in ("date", "seal")},
                "signature": signature.get("status", "未执行"),
            })
        counts = Counter(item["company"] for item in items)
        companies = [{"name": name, "count": count}
                     for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        return {"items": items, "companies": companies, "total": len(items)}

    def project_result(self, item, *, _connection=None):
        """Compare sample evidence against the shared standards, never production result IDs."""
        item["signature_check"] = derive_signature_check(item.get("fields") or {}, item.get("signature_check"))
        # Failed recognition still has a source image available for inspection.
        if not item.get("preview_url"):
            from urllib.parse import quote
            item["preview_url"] = f"/files/samples/original/{quote(item['filename'])}"
        item["sample_center"] = True
        requirement = (item.get("fields") or {}).get("签章要求", "")
        standard = self.ctx.database.get_seal_standard(requirement, _connection=_connection)
        return with_review_revision(apply_standard_check(item, standard))

    def result(self, result_id):
        try:
            return self.project_result(self.database.get_result(result_id))
        except KeyError:
            abort(404)


def register_sample_routes(app, ctx):
    """Defer all database access until initialization or a sample request."""
    bp = Blueprint("samples", __name__)
    lock = threading.Lock()
    runtime = None

    def center():
        nonlocal runtime
        path = ctx.database.path.parent / "sample-center" / "results.db"
        with lock:
            if runtime is None or runtime.database.path != path:
                if runtime:
                    runtime.worker.stop()
                runtime = SampleCenter(ctx)
            return runtime

    @bp.get("/api/samples")
    def listing():
        service = center()
        if app.config["BACKGROUND_WORKER"]:
            service.worker.start()
        return jsonify(service.listing())

    @bp.get("/api/samples/image/<name>")
    def source_image(name):
        source = center().source(name)
        if request.args.get("thumbnail") == "1":
            from PIL import Image, ImageOps
            from io import BytesIO
            image = ImageOps.exif_transpose(Image.open(source))
            image.thumbnail((480, 480))
            output = BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=80)
            output.seek(0)
            return send_file(output, mimetype="image/jpeg", max_age=3600)
        return send_file(source, conditional=True)

    @bp.get("/files/samples/<kind>/<path:name>")
    def sample_file(kind, name):
        service = center()
        if kind == "original":
            return send_file(service.source(name), conditional=True)
        directory = {"previews": service.preview_dir, "artifacts": service.artifact_dir}.get(kind)
        if directory is None:
            abort(404)
        if kind == "artifacts" and request.args.get("preview") == "rgb":
            from .artifact_preview import send_rgb_artifact_preview
            return send_rgb_artifact_preview(directory, name)
        return send_from_directory(directory, name)

    @bp.post("/api/samples/run")
    def run():
        service = center()
        payload = request.get_json(silent=True)
        names = payload.get("filenames") if isinstance(payload, dict) else None
        if (not isinstance(names, list) or not 1 <= len(names) <= 5000
                or any(not isinstance(name, str) for name in names)):
            return jsonify(error="请选择要运行的样单"), 400
        names = list(dict.fromkeys(names))
        catalog = service.catalog()
        if any(name not in catalog for name in names):
            return jsonify(error="部分样单不存在，请刷新列表后重试"), 400
        # Check every source before creating a task.
        for name in names:
            service.source(name)
        settings = ctx.database.get_json_setting("recognition_defaults")
        try:
            options = resolve_recognition_options(
                settings=settings, api_enabled=ctx.analyzer.seal_api.enabled).as_dict()
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400
        result = service.store.enqueue(names, options, settings)
        if app.config["BACKGROUND_WORKER"] and result["accepted"]:
            service.worker.start()
        return jsonify(result), 202

    @bp.get("/api/samples/history")
    def history():
        service = center()
        with service.database.connect() as con:
            rows = con.execute("""SELECT j.filename,j.status,j.created_at,j.finished_at,
                j.error_message,j.result_id FROM recognition_jobs j ORDER BY j.rowid DESC LIMIT 200""").fetchall()
        return jsonify([dict(row) for row in rows])

    @bp.get("/api/samples/statistics")
    def statistics():
        try:
            return jsonify(sample_statistics(center(), request.args.get("task_id", "")))
        except KeyError:
            return jsonify(error="这次测试记录不存在，请重新选择"), 404

    @bp.get("/api/samples/results/<int:result_id>")
    def result(result_id):
        return jsonify(center().result(result_id))

    @bp.post("/api/samples/results/<int:result_id>/seal-standard")
    def save_standard(result_id):
        service = center()
        dependencies = SealStandardDependencies(
            database=ctx.database, artifact_dir=service.artifact_dir,
            project_result=ctx._project_paginated_result,
            review_revision=review_revision, with_review_revision=with_review_revision,
            source_database=service.database, source_project_result=service.project_result,
            artifact_url_prefix="/files/samples/artifacts/", source_kind="sample",
        )
        return register_standard(dependencies, result_id)

    @bp.get("/api/samples/results/<int:result_id>/review-history")
    def review_history(result_id):
        service = center()
        service.result(result_id)
        return jsonify(service.database.history(result_id))

    @bp.get("/api/samples/results/<int:result_id>/ground-truth")
    def ground_truth(result_id):
        service = center()
        name = service.result(result_id)["filename"]
        entry = service.catalog().get(name)
        return jsonify(filename=name, exists=entry is not None, entry=entry, history=[])

    @bp.get("/files/sample-selected-seal/<int:result_id>.png")
    def selected_seal(result_id):
        service = center()
        item = service.result(result_id)
        try:
            image = render_selected_seal(service.source(item["filename"]), item.get("seal_check") or {})
        except (KeyError, ValueError, OSError):
            abort(404)
        return send_file(image, mimetype="image/png", max_age=0)

    app.register_blueprint(bp)
