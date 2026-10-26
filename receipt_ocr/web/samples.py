"""Customer-organized sample library with a separate, durable test database.

The normal recognizer is reused. Tasks, results and generated images are
stored independently from production receipt data.
"""
from collections import Counter
import json
import threading
import uuid

from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory

from receipt_ocr.application import resolve_recognition_options
from receipt_ocr.domain.fields.schema import derive_signature_check
from receipt_ocr.domain.seal_standards import apply_standard_check, requirement_key
from receipt_ocr.jobs.service import ReceiptJobService
from receipt_ocr.jobs.store import JobStore
from receipt_ocr.jobs.worker import JobWorker
from receipt_ocr.persistence.database import Database, now_iso
from receipt_ocr.recognition.seal.providers.qingtong import render_selected_seal
from .results import review_revision, with_review_revision
from .seal_standards import SealStandardDependencies, register_standard
from .sample_statistics import sample_statistics
from .sample_company_marks import SampleCompanyMarks, MarkConflict
from .sample_library import SampleLibrary, LibraryConflict
from .sample_thumbnails import SampleThumbnails
from .sample_annotations import SampleAnnotations
from .sample_requirement_groups import classify_requirements, group_name
from .sample_result_filters import (
    LIST_RESULT_SQL, SEAL_SOURCES, decode_list_result, sample_result_filters, project_sample_seal_result,
)


def sample_urls(value):
    if isinstance(value, dict):
        return {key: sample_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sample_urls(item) for item in value]
    if isinstance(value, str) and value.startswith(("/files/previews/", "/files/artifacts/")):
        return value.replace("/files/", "/files/samples/", 1)
    return value


class SampleJobStore(JobStore):
    def __init__(self, database, library):
        super().__init__(database)
        self.library = library

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
            catalog = self.library.catalog(connection=con)
            if any(name not in catalog for name in names):
                raise LibraryConflict('部分样单已被删除，请刷新列表')
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
        self.library = SampleLibrary(ctx, self.database)
        self.thumbnails = SampleThumbnails(self.database.path.parent / 'thumbnails')
        self.annotations = SampleAnnotations(self.library)
        self.store = SampleJobStore(self.database, self.library)
        self.store.initialize()
        self.company_marks = SampleCompanyMarks(self.database)
        self.preview_dir = self.database.path.parent / "previews"
        self.artifact_dir = self.database.path.parent / "artifacts"
        self.preview_dir.mkdir(exist_ok=True)
        self.artifact_dir.mkdir(exist_ok=True)
        service = ReceiptJobService(
            ctx._configured_analyze, data_dir=ctx.DATA_DIR, upload_dir=self.library.directory,
            preview_dir=self.preview_dir, artifact_dir=self.artifact_dir)

        def execute(job):
            source = self.library.source(job['filename'])
            if source.parent == self.library.directory.resolve():
                job = {**job, 'stored_name': source.name}
            result, preview = service(job)
            return sample_urls(result), preview

        self.worker = JobWorker(self.store, execute, logger=ctx.app.logger)

    def catalog(self):
        return self.library.catalog()

    def source(self, name, *, include_deleted=False):
        try:
            return self.library.source(name, include_deleted=include_deleted)
        except KeyError:
            abort(404)

    def listing(self):
        catalog = self.catalog()
        # Only the latest job per filename is needed, not full OCR artifacts.
        with self.database.connect() as con:
            con.execute('BEGIN')
            jobs = {row["filename"]: dict(row) for row in con.execute(f"""
                SELECT j.filename,j.status,j.result_id,j.error_message,j.created_at,j.finished_at,j.payload_json,
                    {LIST_RESULT_SQL} AS list_result
                FROM recognition_jobs j
                LEFT JOIN results r ON r.id=j.result_id AND r.deleted_at=''
                WHERE j.rowid IN (SELECT MAX(rowid) FROM recognition_jobs GROUP BY filename)
            """)}
            marks, versions = self.company_marks.snapshot(catalog, con)
        items = []
        for name, entry in catalog.items():
            job = jobs.get(name, {})
            result = decode_list_result(job['list_result']) if job else {}
            signature = derive_signature_check(result.get('fields') or {}, result.get('signature_check') or {})
            status = {"queued": "测试中", "running": "测试中", "succeeded": "已完成",
                      "failed": "运行失败"}.get(job.get("status"), "未测试")
            filters = sample_result_filters(job, entry, result=result)
            items.append({
                "filename": name,
                "company": str((entry.get("fields") or {}).get("客户名称") or "").strip() or "未分类",
                "requirement": group_name(entry),
                "requirement_key": requirement_key(group_name(entry)),
                "status": status, "job_status": job.get("status", ""),
                "result_id": job.get("result_id"), "error": job.get("error_message", ""),
                "updated_at": job.get("finished_at") or job.get("created_at", ""),
                "checks": {"date": filters['date']['state'], "seal": filters['seals']['combined']['state']},
                "signature": signature.get("status", "未执行"),
                "result_filters": filters,
            })
        counts = Counter(item["company"] for item in items)
        companies = [{"name": name, "count": count}
                     for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
        # Customer names can differ from the stamp owner. Use the same exact
        # normalized requirement key as the seal center, never a name substring.
        with self.ctx.database.connect() as con:
            standard_keys = {row[0] for row in con.execute('SELECT requirement_key FROM seal_standards')}
        requirements = {name: set() for name in counts}
        unlabeled = Counter()
        for entry in catalog.values():
            fields = entry.get('fields') or {}
            name = str(fields.get('客户名称') or '').strip() or '未分类'
            key = requirement_key(fields.get('签章要求'))
            if key:
                requirements[name].add(key)
            else:
                unlabeled[name] += 1
        seals = {}
        for name, keys in requirements.items():
            matched = len(keys & standard_keys)
            seals[name] = {'matched': matched, 'total': len(keys), 'unlabeled': unlabeled[name],
                           'has_standard': matched > 0,
                           'complete': bool(keys) and matched == len(keys) and not unlabeled[name]}
        groups = {name: {} for name in counts}
        for item in items:
            key = item['requirement_key']
            group = groups[item['company']].setdefault(key, {
                'key': key, 'name': item['requirement'] or '未归类', 'count': 0,
                'has_standard': bool(key and key in standard_keys)})
            group['count'] += 1
        return {"items": items, "companies": companies, "total": len(items),
                "filter_seal_sources": SEAL_SOURCES,
                "requirement_groups": {name: sorted(values.values(), key=lambda group: (bool(group['key']), group['name']))
                                       for name, values in groups.items()},
                "company_marks": marks, "company_versions": versions, "company_seals": seals}

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
        return with_review_revision(project_sample_seal_result(apply_standard_check(item, standard)))

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
        service = center()
        source = service.source(name)
        if request.args.get("thumbnail") == "1":
            from io import BytesIO
            try:
                thumbnail, etag = service.thumbnails.get(source)
            except FileNotFoundError:
                abort(404)
            return send_file(BytesIO(thumbnail), mimetype="image/jpeg", conditional=True,
                             etag=etag, max_age=0)
        return send_file(source, conditional=True)

    @bp.post('/api/samples/import')
    def import_samples():
        try:
            return jsonify(center().library.import_files(request.files.getlist('files'),
                                                         request.form.get('company', ''))), 201
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.post('/api/samples/delete')
    def delete_samples():
        payload = request.get_json(silent=True)
        try:
            return jsonify(center().library.remove(payload.get('filenames') if isinstance(payload, dict) else None))
        except LibraryConflict as exc:
            return jsonify(error=str(exc)), 409
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.post('/api/samples/classify')
    def classify_samples():
        payload = request.get_json(silent=True)
        if payload is not None and not isinstance(payload, dict):
            return jsonify(error='归类范围无效'), 400
        payload = payload or {}
        try:
            return jsonify(center().library.classify(company=payload.get('company', '未分类'),
                           requirement=payload.get('requirement'), filenames=payload.get('filenames')))
        except KeyError:
            return jsonify(error='客户公司不存在，请刷新列表'), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.post('/api/samples/classify-requirements')
    def classify_company_requirements():
        payload = request.get_json(silent=True)
        try:
            return jsonify(classify_requirements(center().library,
                           payload.get('company') if isinstance(payload, dict) else None,
                           requirement=payload.get('requirement') if isinstance(payload, dict) else None,
                           filenames=payload.get('filenames') if isinstance(payload, dict) else None))
        except KeyError:
            return jsonify(error='客户公司不存在，请刷新列表'), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.post('/api/samples/company-mark')
    def company_mark():
        service = center()
        try:
            return jsonify(service.company_marks.save(service.catalog(), request.get_json(silent=True)))
        except MarkConflict as exc:
            return jsonify(error=str(exc)), 409
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except KeyError:
            return jsonify(error='客户公司不存在，请刷新列表'), 404

    @bp.get("/files/samples/<kind>/<path:name>")
    def sample_file(kind, name):
        service = center()
        if kind == "original":
            return send_file(service.source(name, include_deleted=True), conditional=True)
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
        try:
            result = service.store.enqueue(names, options, settings)
        except LibraryConflict as exc:
            return jsonify(error=str(exc)), 409
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

    @bp.get('/api/samples/statistics/export')
    def export_statistics():
        from .sample_statistics_export import export_statistics as build_export
        try:
            output = build_export(center(), company=request.args.get('company', ''),
                                  requirement=request.args.get('requirement', ''),
                                  task_id=request.args.get('task_id', ''),
                                  tab=request.args.get('tab', 'seal'), selected=request.args.get('filter', ''),
                                  channel=request.args.get('channel', ''))
        except KeyError:
            return jsonify(error='这次测试记录不存在，请重新选择'), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name='样单统计结果.xlsx', max_age=0)

    @bp.get("/api/samples/results/<int:result_id>")
    def result(result_id):
        return jsonify(center().result(result_id))

    @bp.get("/api/samples/results/<int:result_id>/recognition-calls")
    def result_recognition_calls(result_id):
        from .recognition_calls import recognition_calls_response
        return recognition_calls_response(center().database, result_id, samples=True)

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
        name = service.result(result_id)['filename']
        rows = service.database.history(result_id) + service.database.ground_truth_history(name)
        return jsonify(sorted(rows, key=lambda row: (row['changed_at'], row['id']), reverse=True))

    @bp.get("/api/samples/results/<int:result_id>/ground-truth")
    def ground_truth(result_id):
        service = center()
        name = service.result(result_id)["filename"]
        return jsonify(service.annotations.snapshot(name, result_id))

    @bp.patch('/api/samples/results/<int:result_id>/annotations')
    def save_annotations(result_id):
        try:
            return jsonify(center().annotations.save(result_id, request.get_json(silent=True)))
        except LibraryConflict as exc:
            return jsonify(error=str(exc), code='sample_annotation_conflict'), 409
        except KeyError:
            abort(404)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.get("/files/sample-selected-seal/<int:result_id>.png")
    def selected_seal(result_id):
        service = center()
        item = service.result(result_id)
        try:
            image = render_selected_seal(service.source(item["filename"], include_deleted=True), item.get("seal_check") or {})
        except (KeyError, ValueError, OSError):
            abort(404)
        return send_file(image, mimetype="image/png", max_age=0)

    from .bootstrap_data import register_bootstrap_routes
    register_bootstrap_routes(bp, ctx, center)
    app.register_blueprint(bp)
