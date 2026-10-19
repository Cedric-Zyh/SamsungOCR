"""Unified daily receipt read model, including files without OCR results.

Queue identity is never inferred from filenames. Filters/counts/pagination are
applied after the latest job has overlaid its logical receipt.
"""
from datetime import date

from ..persistence.results import _matches_filters
from ..persistence.database import DEFAULT_RECOGNITION_SETTINGS
from ..persistence.read_model import load_selected_receipts
from ..domain.documents.pagination import merge_paginated_results


ACTIVE = {"awaiting_upload", "ready", "queued", "running"}
LABELS = {"awaiting_upload": "待上传", "ready": "待开始", "queued": "排队中", "running": "识别中"}


def management_jobs(ctx, day, rows=None):
    """Include later retries when looking at a receipt's original import day."""
    if rows is None:
        rows = ctx.database.query_receipts(filters={"import_date": day}, latest_by_filename=True, summary_only=True)["items"]
    ids = sorted({key for row in rows for key in
                  [row["id"], *(row.get("page_group") or {}).get("continuation_result_ids", [])]})
    jobs = {job["id"]: job for job in ctx.job_store.daily(day)}
    order = {}
    settings = ctx.database.get_json_setting('recognition_defaults', DEFAULT_RECOGNITION_SETTINGS)
    with ctx.database.connect() as connection:
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            placeholders = ','.join('?' for _ in chunk)
            for raw in connection.execute(
                f"SELECT j.*,j.rowid AS task_order FROM recognition_jobs j "
                f"WHERE result_id IN ({placeholders}) OR target_result_id IN ({placeholders})", chunk + chunk):
                raw = dict(raw)
                jobs[raw['id']] = ctx.job_store.public(raw, settings)
                order[raw['id']] = raw['task_order']
    return sorted(jobs.values(), key=lambda job: (job['created_at'], order.get(job['id'], 0)), reverse=True)


def management_listing(ctx, filters, page, page_size, *, reviewable=False, selected_ids=None, deferred_ids=None):
    day = filters.get("import_date", "")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("请选择有效的导入日期")
    rows = ctx.database.query_receipts(filters={"import_date": day}, latest_by_filename=True, summary_only=True)["items"]
    by_id = {}
    for row in rows:
        for key in [row["id"], *(row.get("page_group") or {}).get("continuation_result_ids", [])]:
            by_id[key] = row
    jobs = management_jobs(ctx, day, rows)
    chosen = {}
    pending = []
    for job in jobs:
        if job["status"] == "cancelled":
            continue
        result_id = job.get("result_id") or job.get("target_result_id")
        row = by_id.get(result_id)
        if result_id and row is None:
            try:
                row = ctx.project_result(ctx.database.get_result(result_id))
            except KeyError:
                continue
            if row["id"] not in by_id:
                rows.append(row)
            for key in [row["id"], *(row.get("page_group") or {}).get("continuation_result_ids", [])]:
                by_id[key] = row
        if row is None:
            # A result can be committed between the two reads; its queue id
            # will reconcile it on the next request without filename merging.
            pending.append({"id": None, "filename": job["filename"], "task_id": job["task_id"],
                            "created_at": job["created_at"], "fields": {}, "processing_job": job})
            continue
        previous = chosen.get(row["id"])
        # A running continuation also blocks review of the merged cover.
        if previous is None or (previous["status"] not in ACTIVE and job["status"] in ACTIVE):
            chosen[row["id"]] = job
    projected = []
    for original in rows + pending:
        row = dict(original)
        job = row.get("processing_job") or chosen.get(row["id"])
        if job:
            row["processing_job"] = job
            row["management_task_id"] = job["task_id"]
        status = job["status"] if job else ""
        if status in ACTIVE or status == "failed":
            row["processing_status"] = status
            row["overall"] = row["final_result"] = "识别失败" if status == "failed" else LABELS[status]
            row["review_status"] = ""
            # Last attempt's checks must not match current-result filters.
            for key in ("date_check", "seal_check", "signature_check"):
                row[key] = {"status": "待识别"}
        projected.append(row)

    def matches(row, wanted):
        wanted = dict(wanted)
        overall = wanted.pop("overall", "")
        if overall == "处理中":
            if row.get("processing_status") not in ACTIVE:
                return False
        elif overall and overall != (row.get("final_result") or row.get("overall")):
            return False
        # The daily queue can include retries of an older imported receipt.
        wanted.pop("import_date", None)
        task = wanted.pop("task_id", "")
        if task and task not in (row.get("task_id"), row.get("management_task_id")):
            return False
        return _matches_filters(row, wanted)

    scope = {key: value for key, value in filters.items() if key != "overall"}
    scoped = [row for row in projected if matches(row, scope)]
    counts = {key: 0 for key in ("", "处理中", "通过", "需人工复核", "不通过", "识别失败")}
    for row in scoped:
        counts[""] += 1
        status = "处理中" if row.get("processing_status") in ACTIVE else row.get("final_result") or row.get("overall")
        if status in counts:
            counts[status] += 1
    filtered = [row for row in scoped if matches(row, filters)]
    if selected_ids is not None:
        filtered = [row for row in filtered if selected_ids.intersection(
            {row.get("id"), *(row.get("page_group") or {}).get("continuation_result_ids", [])})]
    from .results import reviewable_results, deferred_scope_metadata
    if reviewable:
        filtered = reviewable_results(ctx, filtered)
    metadata = deferred_scope_metadata(filtered, deferred_ids)
    filtered.sort(key=lambda row: (row.get("created_at", ""), row.get("id") or 0,
                                  (row.get("processing_job") or {}).get("id", "")), reverse=True)
    offset = (page - 1) * page_size
    selected = filtered[offset:offset + page_size]
    # Decode expensive OCR evidence only for the visible page, after counting
    # and filtering both saved results and pending uploads together.
    with ctx.database.connect() as connection:
        full = {row["id"]: row for row in load_selected_receipts(connection,
            [row for row in selected if row.get("id")], ctx.database._row_to_result, merge_paginated_results)}
    output = []
    for row in selected:
        hydrated = full.get(row.get("id"), row)
        for key in ("processing_job", "processing_status", "management_task_id"):
            if key in row:
                hydrated[key] = row[key]
        if row.get("processing_status"):
            for key in ("overall", "final_result", "review_status", "date_check", "seal_check", "signature_check"):
                hydrated[key] = row[key]
        output.append(hydrated)
    return {"items": output, "total": len(filtered),
            "page": page, "page_size": page_size, "status_counts": counts, **metadata}
