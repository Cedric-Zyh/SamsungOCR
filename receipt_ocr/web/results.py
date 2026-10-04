"""HTTP handlers for receipt listing, pagination, and result details."""

from __future__ import annotations

from .dependencies import ResultDependencies

from datetime import date, datetime
import hashlib
import json

from flask import abort, jsonify, request


def page_options():
    try:
        page = int(request.args.get("page", "1"))
        page_size = int(request.args.get("page_size", "100"))
    except ValueError:
        raise ValueError("页码和每页数量必须为正整数") from None
    if page < 1 or not 1 <= page_size <= 2000:
        raise ValueError("页码必须大于零，每页数量必须在 1 至 2000 之间")
    return page, page_size


def reviewable_results(ctx: ResultDependencies, rows):
    """Filter logical receipts before pagination, including busy linked pages."""
    with ctx.database.connect() as connection:
        has_jobs = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recognition_jobs'"
        ).fetchone()
        busy = set()
        if has_jobs:
            for job in connection.execute(
                "SELECT result_id,target_result_id FROM recognition_jobs "
                "WHERE status IN ('awaiting_upload','queued','running')"
            ):
                busy.update(value for value in job if value)
    return [row for row in rows if row.get("review_status") == "待复核"
            and not row.get("error_message") and row.get("overall") != "识别失败"
            and not busy.intersection({row["id"], *(row.get("page_group") or {}).get("continuation_result_ids", [])})]


def selected_result_ids(parameter="ids"):
    """An explicit empty selection stays empty; malformed scopes never broaden."""
    if parameter not in request.args:
        return None
    values = request.args.getlist(parameter)
    if len(values) != 1:
        raise ValueError("选中的回单编号格式无效")
    if values[0] == "":
        return set()
    ids = set()
    for value in values[0].split(","):
        if not value.isascii() or not value.isdigit() or value.startswith("0") or len(value) > 19:
            raise ValueError("选中的回单编号必须为正整数")
        result_id = int(value)
        if result_id > 9223372036854775807:
            raise ValueError("选中的回单编号超出有效范围")
        ids.add(result_id)
    return ids


def deferred_scope_metadata(rows, deferred_ids):
    if deferred_ids is None:
        return {}
    return {"deferred_in_scope_ids": [row["id"] for row in rows
            if deferred_ids.intersection({row["id"],
                *(row.get("page_group") or {}).get("continuation_result_ids", [])})]}


def receipt_listing(ctx: ResultDependencies, *, latest_by_filename):
    filters = {key: request.args.get(key, "") for key in ctx.result_filters}
    paged = "page" in request.args or "page_size" in request.args
    try:
        page, page_size = page_options() if paged else (1, min(max(request.args.get("limit", 200, type=int), 1), 2000))
        selected_ids = selected_result_ids()
        deferred_ids = selected_result_ids("deferred_ids")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    offset = (page - 1) * page_size
    if request.args.get("reviewable") == "1" or selected_ids is not None or deferred_ids is not None:
        rows = ctx.database.query_receipts(
            filters=filters, latest_by_filename=latest_by_filename,
            ordering="review" if latest_by_filename else "id",
        )["items"]
        if selected_ids is not None:
            rows = [row for row in rows if selected_ids.intersection({row["id"],
                    *(row.get("page_group") or {}).get("continuation_result_ids", [])})]
        if request.args.get("reviewable") == "1":
            rows = reviewable_results(ctx, rows)
        result = {"items": rows[offset:offset + page_size], "total": len(rows),
                  **deferred_scope_metadata(rows, deferred_ids)}
    else:
        result = ctx.database.query_receipts(
            filters=filters, latest_by_filename=latest_by_filename,
            limit=page_size, offset=offset,
            ordering="review" if latest_by_filename else "id",
        )
    return jsonify({**result, "page": page, "page_size": page_size} if paged else result["items"])


def daily_results(ctx: ResultDependencies):
    day = request.args.get("import_date", "")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        return jsonify(error="请选择有效的导入日期"), 400
    try:
        deferred_ids = selected_result_ids("deferred_ids")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    rows = ctx.database.query_receipts(filters={"import_date": day}, latest_by_filename=True)["items"]
    if request.args.get("include_queue") == "1":
        seen = {row["id"] for row in rows}
        for job in ctx.job_store.daily(day):
            result_id = job.get("result_id") or job.get("target_result_id")
            if not result_id or result_id in seen:
                continue
            try:
                projected = ctx.project_result(ctx.database.get_result(result_id))
                if projected["id"] not in seen:
                    rows.append(projected)
                    seen.add(projected["id"])
            except KeyError:
                continue
    if request.args.get("reviewable") == "1":
        rows = reviewable_results(ctx, rows)
    rows.sort(key=lambda row: row["id"], reverse=True)
    if "page" in request.args or "page_size" in request.args:
        try:
            page, page_size = page_options()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(items=rows[(page - 1) * page_size:page * page_size],
                       total=len(rows), page=page, page_size=page_size,
                       **deferred_scope_metadata(rows, deferred_ids))
    return jsonify(rows)


def list_results(ctx: ResultDependencies):
    return receipt_listing(ctx, latest_by_filename=True)


def import_dates(ctx: ResultDependencies):
    month = request.args.get("month", "")
    try:
        parsed = datetime.strptime(month, "%Y-%m")
        if parsed.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        return jsonify({"error": "请选择有效月份"}), 400
    return jsonify(ctx.database.import_date_counts(month, request.args.get("ocr_backend", "")))


def list_result_history(ctx: ResultDependencies):
    return receipt_listing(ctx, latest_by_filename=False)


def review_revision(result):
    evidence = {key: value for key, value in result.items() if key not in {"review_revision", "ground_truth_saved"}}
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def with_review_revision(result):
    return {**result, "review_revision": review_revision(result)}


def get_result(ctx: ResultDependencies, result_id: int):
    try:
        return jsonify(with_review_revision(ctx.project_result(ctx.database.get_result(result_id))))
    except KeyError:
        abort(404)


def review_history(ctx: ResultDependencies, result_id: int):
    return jsonify(ctx.database.history(result_id))


def result_ground_truth(ctx: ResultDependencies, result_id: int):
    try:
        current = ctx.database.get_result(result_id)
    except KeyError:
        abort(404)
    truth = ctx.load_ground_truth(ctx.ground_truth_path)
    filename = current["filename"]
    return jsonify({
        "filename": filename,
        "exists": filename in truth,
        "entry": truth.get(filename),
        "history": ctx.database.ground_truth_history(filename),
    })
