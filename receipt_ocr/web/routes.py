"""HTTP route registration without mutable module dependencies."""

from __future__ import annotations


def register_routes(app, handlers, *, before_request) -> None:
    """Attach explicit endpoint functions and the initialization hook."""
    app.before_request(before_request)

    routes = [
        ("/", "index", ("GET",)),
        ("/api/ocr-backends", "ocr_backends", ("GET",)),
        ("/api/settings", "get_settings", ("GET",)),
        ("/api/settings", "update_settings", ("PATCH",)),
        ("/api/settings/retention", "retention_settings", ("GET", "PATCH")),
        ("/api/settings/retention-days", "retention_settings", ("GET", "PATCH")),
        ("/api/settings/data-retention", "retention_settings", ("GET", "PATCH")),
        ("/api/settings/recognition", "get_recognition_settings", ("GET",)),
        ("/api/settings/recognition", "update_recognition_settings", ("PATCH",)),
        ("/api/tasks", "create_task", ("POST",)),
        ("/api/tasks", "list_tasks", ("GET",)),
        ("/api/results/delete", "delete_results", ("POST",)),
        ("/api/tasks/<task_id>", "get_task", ("GET",)),
        ("/api/queue", "queue_progress", ("GET",)),
        ("/api/queue/control", "queue_control", ("GET", "POST")),
        ("/api/jobs/<job_id>/upload", "upload_job", ("POST",)),
        ("/api/jobs/start", "start_jobs", ("POST",)),
        ("/api/jobs/<job_id>/retry", "retry_job", ("POST",)),
        ("/api/jobs/<job_id>/cancel", "cancel_job", ("POST",)),
        ("/api/jobs/cancel", "cancel_jobs", ("POST",)),
        ("/api/process-history", "process_history", ("GET",)),
        ("/api/analyze", "analyze_upload", ("POST",)),
        ("/api/daily-results", "daily_results", ("GET",)),
        ("/api/results", "list_results", ("GET",)),
        ("/api/import-dates", "import_dates", ("GET",)),
        ("/api/history", "list_result_history", ("GET",)),
        ("/api/results/<int:result_id>", "get_result", ("GET",)),
        ("/api/results/<int:result_id>/review", "review_result", ("PATCH",)),
        ("/api/results/<int:result_id>/review-history", "review_history", ("GET",)),
        ("/api/results/<int:result_id>/ground-truth", "result_ground_truth", ("GET",)),
        ("/api/results/<int:result_id>/retry", "retry_result", ("POST",)),
        ("/api/results/bulk-review", "bulk_review", ("POST",)),
        ("/api/results/bulk-retry", "bulk_retry", ("POST",)),
        ("/api/report", "report", ("GET",)),
        ("/files/selected-seal/<int:result_id>.png", "selected_seal_preview", ("GET",)),
        ("/files/<kind>/<path:name>", "files", ("GET",)),
    ]
    endpoint_counts: dict[str, int] = {}
    for rule, name, methods in routes:
        count = endpoint_counts.get(name, 0)
        endpoint_counts[name] = count + 1
        endpoint = name if count == 0 else f"{name}_{count}"
        app.add_url_rule(rule, endpoint, handlers[name], methods=list(methods))
