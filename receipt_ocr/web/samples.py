"""HTTP adapters for the independently stored sample library."""
from flask import Blueprint, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import NotFound

from receipt_ocr.api.revisions import review_revision, with_review_revision
from receipt_ocr.recognition.seal.providers.qingtong import render_selected_seal
from receipt_ocr.samples.runtime import SampleRuntime
from receipt_ocr.samples.service import SampleNotFound, SampleUnavailable
from receipt_ocr.samples.library import LibraryConflict
from receipt_ocr.samples.company_marks import MarkConflict
from receipt_ocr.samples.requirement_groups import classify_requirements
from receipt_ocr.samples.statistics import sample_statistics
from .seal_standards import SealStandardDependencies, register_standard


def register_sample_routes(app, get_dependencies):
    """Defer all database access until initialization or a sample request."""
    bp = Blueprint("samples", __name__)
    runtime = SampleRuntime(get_dependencies)
    app.extensions['sample_runtime'] = runtime
    def center():
        return app.extensions['sample_runtime'].get()

    @bp.errorhandler(SampleNotFound)
    def missing_sample(error):
        return NotFound()

    @bp.errorhandler(SampleUnavailable)
    def unavailable_sample(error):
        return jsonify(error=str(error)), 503

    @bp.get("/api/samples")
    def listing():
        service = center()
        if app.config["BACKGROUND_WORKER"]:
            service.start_worker()
        response = jsonify(service.listing(request.args.get('revision')))
        response.headers['Cache-Control'] = 'no-store'
        return response

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
        dependencies = get_dependencies()
        settings = dependencies.database.get_json_setting("recognition_defaults")
        try:
            result = service.run(names, payload.get('mode') if isinstance(payload, dict) else None,
                                 settings=settings, api_enabled=dependencies.seal_api_enabled)
        except LibraryConflict as exc:
            return jsonify(error=str(exc)), 409
        except SampleUnavailable:
            raise
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400
        if app.config["BACKGROUND_WORKER"] and result["accepted"]:
            service.start_worker()
        return jsonify(result), 202

    @bp.get('/api/samples/tasks/<task_id>')
    def run_progress(task_id):
        from receipt_ocr.samples.progress import sample_run_progress
        try:
            return jsonify(sample_run_progress(center().store, task_id))
        except KeyError:
            return jsonify(error='这次样单测试已不存在'), 404

    @bp.get("/api/samples/history")
    def history():
        return jsonify(center().history())

    @bp.get("/api/samples/statistics")
    def statistics():
        try:
            return jsonify(sample_statistics(center(), request.args.get("task_id", "")))
        except KeyError:
            return jsonify(error="这次测试记录不存在，请重新选择"), 404

    @bp.get('/api/samples/statistics/export')
    def export_statistics():
        from receipt_ocr.samples.statistics_export import export_statistics as build_export
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
        inputs = get_dependencies()
        dependencies = SealStandardDependencies(
            database=inputs.database, artifact_dir=service.artifact_dir,
            project_result=inputs.project_result,
            review_revision=review_revision, with_review_revision=with_review_revision,
            source_database=service.database, source_project_result=service.project_result,
            artifact_url_prefix="/files/samples/artifacts/", source_kind="sample",
        )
        return register_standard(dependencies, result_id)

    @bp.get("/api/samples/results/<int:result_id>/review-history")
    def review_history(result_id):
        return jsonify(center().review_history(result_id))

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
    register_bootstrap_routes(bp, get_dependencies, center)
    from .danzhengtong_jobs import register_danzhengtong_routes
    register_danzhengtong_routes(bp, lambda: center().store, lambda: center().start_worker(), prefix='/api/samples')
    app.register_blueprint(bp)
