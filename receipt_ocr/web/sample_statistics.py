"""Read-only evaluation of sample jobs against human annotations.

Seal annotations describe the expected decision, not a transcription of the
actual stamp. Keep that distinction explicit and never use the merged verdict
as the result of an individual provider channel.
"""
import json
from datetime import date
import re
from collections import Counter

from receipt_ocr.domain.sample_problems import resolve_problem_annotations
from receipt_ocr.domain.sample_seal_decision import sample_seal_decision
from receipt_ocr.providers.registry import PROVIDERS


def _date(value):
    text = str(value or "").strip()
    parts = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if parts:
        try:
            return date(*map(int, parts.groups())).isoformat()
        except ValueError:
            pass
    return text


def _cell(state, value="", decision="", note=""):
    return {"state": state, "value": str(value or ""), "decision": decision, "note": note}


def _evaluate(check, *, value, expected, date_channel=False, error="", missing="未运行", requirement=""):
    decision = str(check.get("status") or "")
    if missing != "executed":
        return _cell(missing, value, decision)
    if not date_channel and not error and decision != "识别失败":
        decision = sample_seal_decision(check, value=value, requirement=requirement)
    if expected is None:
        return _cell("待标注", value, decision, error)
    if error or decision == "识别失败":
        return _cell("失败", value, decision, error or check.get("message", ""))
    if date_channel:
        # A human-marked blank date is a legitimate expected observation.
        if _date(value) == _date(expected):
            return _cell("正确", value, decision)
        return _cell("错误" if value else "未识别", value, decision)
    if not value:
        return _cell("未识别", value, decision)
    if decision not in {"匹配", "不匹配", "部分匹配"}:
        return _cell("错误", value, decision, "未形成明确判定")
    return _cell("正确" if decision == expected else "错误", value, decision)


def _local(method):
    provider = PROVIDERS.get(method)
    return bool(provider and provider.kind in {"local_text", "local_seal"}) or method == "legacy_local"


def _local_label(method):
    provider = PROVIDERS.get(method)
    return f"本地 · {provider.label}" if provider else "本地 · 印章识别"


def evaluate_sample(name, truth, job):
    result = json.loads(job.get("result_json") or "{}")
    payload = json.loads(job.get("payload_json") or "{}")
    options = payload.get("sample_options") or payload
    config = options.get("recognition_config")
    if not isinstance(config, dict):
        config = result.get("recognition_config")
    config = config if isinstance(config, dict) else None
    variants = (result.get("recognition_variants") or {}).get("seal") or []
    primary = result.get("seal_check") or {}
    methods = list((config or {}).get("seal") or [])
    if config is None:
        methods = [v["method"] for v in variants]
        mode = options.get("seal_recognition_mode") or result.get("seal_recognition_mode")
        if primary.get("dual_check") or mode in {"qingtong", "qingtong_only"}:
            methods.append("qingtong")
        if (primary.get("local_channel") or mode in {"qingtong", "local"}
                or primary.get("recognition_mode") == "local"
                or str(primary.get("backend", "")).startswith("本地")) and not any(_local(method) for method in methods):
            methods.append("legacy_local")
    local_methods = list(dict.fromkeys(method for method in methods if _local(method)))
    if not local_methods:
        local_methods = ["legacy_local"]  # Keep an explicit "未运行" local column.
    status = job.get("status", "")
    pending = status in {"queued", "running", "awaiting_upload"}
    terminal = status in {"succeeded", "failed"}

    def availability(enabled):
        if not job:
            return "未测试"
        if not enabled:
            return "未运行"
        if pending:
            return "测试中"
        return "executed" if terminal else "未运行"

    failure = job.get("error_message") or ("本次测试失败" if status == "failed" else "")
    fields = truth.get("fields") or {}
    requirement = (result.get("fields") or {}).get("签章要求") or fields.get("签章要求", "")
    expected_seal = ("匹配" if truth["seal_should_match"] else "不匹配") if isinstance(truth.get("seal_should_match"), bool) else None
    expected_date = ("" if truth.get("date_present") is False else truth.get("actual_date"))
    date_check = result.get("date_check") or {}
    date_enabled = bool(config.get("date")) if config is not None else bool(date_check)
    date_cell = _evaluate(date_check, value=date_check.get("actual", ""), expected=expected_date,
                          date_channel=True, error=failure, missing=availability(date_enabled))

    remote_variant = next((v for v in variants if v.get("method") == "qingtong"), None)
    remote = ((remote_variant.get("details") or {}).get("seal_check") or {}) if remote_variant else (
        primary if primary.get("dual_check") else {})
    selected = (remote.get("dual_check") or {}).get("selected") or {}
    seals = {}
    for key, field, comparison in (("template", "label", "requirement_match"), ("ocr", "text", "comparison")):
        check = selected.get(key) or {}
        decision = dict(check.get(comparison) or check)
        if not decision.get("status") and isinstance(check.get("matched"), bool):
            decision["status"] = "匹配" if check["matched"] else "不匹配"
        remote_error = (remote_variant or {}).get("error") or (
            (remote.get("message") or "清瞳识别失败") if remote.get("status") == "识别失败" else "")
        seals[f"qingtong_{key}"] = _evaluate(
            decision, value=check.get(field, ""), expected=expected_seal,
            error=remote_error or failure, missing=availability("qingtong" in methods), requirement=requirement)
    for method in local_methods:
        variant = next((v for v in variants if v.get("method") == method), None)
        fallback = {}
        if not variants:
            fallback = primary.get("local_channel") or (
                primary if len(local_methods) == 1 and all(_local(method) for method in methods) else {})
        check = ((variant.get("details") or {}).get("seal_check") or {}) if variant else fallback
        check = check.get("local_channel") or check
        # Never assemble unrelated candidate strings into a synthetic reading.
        seals[f"local:{method}"] = _evaluate(
            check, value=check.get("recognized") or check.get("display_text", ""),
            expected=expected_seal, error=(variant or {}).get("error") or failure,
            missing=availability(method in methods), requirement=requirement)
    return {
        "filename": name, "company": str(fields.get("客户名称") or "").strip() or "未分类",
        "result_id": job.get("result_id"), "job_status": status,
        "task_id": job.get("task_id", ""), "task_name": job.get("task_name", ""),
        "tested_at": job.get("finished_at") or job.get("started_at") or job.get("created_at", ""),
        "expected_date": expected_date, "requirement": fields.get("签章要求", ""),
        "expected_seal": expected_seal, "date": date_cell, "seals": seals,
        "human_note": truth.get("human_note", ""),
        **resolve_problem_annotations(truth, job.get("result_id")),
    }


def summarize_problem_annotations(rows):
    """Count human observations independently of machine evaluation or channels."""
    statuses = Counter()
    types, features, legacy = Counter(), Counter(), Counter()
    legacy_samples = 0
    # A sample may have several tags, but must count once for each individual tag.
    members = {row['filename']: row for row in rows}.values()
    for row in members:
        review = row.get('problem_review') or {}
        status = review.get('status', 'unmarked')
        statuses[status if status in {'clear', 'issues'} else 'unmarked'] += 1
        if status == 'issues':
            types.update(set(row.get('problem_review', {}).get('types') or []))
        features.update(set(row.get('sample_features') or []))
        old = set(row.get('legacy_problem_types') or [])
        legacy.update(old)
        legacy_samples += bool(old)
    ordered = lambda counter: [{'type': label, 'count': count}
                               for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]
    return {'statuses': {key: statuses[key] for key in ('unmarked', 'clear', 'issues')},
            'types': ordered(types), 'features': ordered(features), 'legacy_types': ordered(legacy),
            'legacy_samples': legacy_samples, 'total': sum(statuses.values())}


def _batch_metadata(batch):
    """Expose the saved test scheme, never the user's current defaults."""
    config = json.loads(batch.get("recognition_config") or "null")
    if not isinstance(config, dict):
        # Older batches predate explicit per-stage plans.
        mode = batch.get("seal_recognition_mode")
        local = batch.get("ocr_backend")
        config = {
            "date": [local] if local else [],
            "seal": (["qingtong"] if mode in {"qingtong", "qingtong_only"} else [])
                    + ([local] if local and mode != "qingtong_only" else []),
        }
    batch["recognition_config"] = config
    batch["scheme_labels"] = {
        stage: [PROVIDERS.get(method).label if PROVIDERS.get(method) else method
                for method in config.get(stage, [])]
        for stage in ("date", "seal")
    }
    return batch


def sample_statistics(center, task_id=""):
    catalog = center.catalog()
    with center.database.connect() as con:
        # All values in this view belong to one database snapshot.
        con.execute("BEGIN")
        batches = [_batch_metadata(dict(row)) for row in con.execute("""
            SELECT b.id,b.name,b.created_at,b.updated_at,b.total,b.status,
                b.completed,b.succeeded,b.failed,b.recognition_config,
                b.ocr_backend,b.seal_recognition_mode,
                COALESCE(MIN(NULLIF(j.started_at,'')),'') AS started_at,
                CASE WHEN b.completed>=b.total THEN COALESCE(MAX(j.finished_at),'')
                     ELSE '' END AS finished_at
            FROM batch_tasks b LEFT JOIN recognition_jobs j ON j.task_id=b.id
            GROUP BY b.id ORDER BY b.rowid DESC
        """)]
        batch_lookup = {batch["id"]: batch for batch in batches}
        for batch in batches:
            batch["status_counts"] = {}
        for count in con.execute("""
            SELECT task_id,status,COUNT(*) AS total FROM recognition_jobs GROUP BY task_id,status
        """):
            if count["task_id"] in batch_lookup:
                batch_lookup[count["task_id"]]["status_counts"][count["status"]] = count["total"]
        if task_id and task_id not in {batch["id"] for batch in batches}:
            raise KeyError(task_id)
        # Snapshot configuration and raw results belong to the same job.
        jobs = {row["filename"]: dict(row) for row in con.execute("""
            SELECT j.filename,j.status,j.result_id,j.error_message,j.payload_json,r.result_json,
                j.task_id,b.name AS task_name,j.created_at,j.started_at,j.finished_at
            FROM recognition_jobs j
            LEFT JOIN results r ON r.id=j.result_id AND r.deleted_at=''
            LEFT JOIN batch_tasks b ON b.id=j.task_id
            WHERE j.rowid IN (
                SELECT MAX(rowid) FROM recognition_jobs
                WHERE (?='' OR task_id=?) GROUP BY filename
            )
        """, (task_id, task_id))}
    rows = [evaluate_sample(name, truth, jobs.get(name, {}))
            for name, truth in catalog.items() if not task_id or name in jobs]
    local_keys = sorted({key for row in rows for key in row["seals"] if key.startswith("local:")
                         and key != "local:legacy_local"})
    if not local_keys or any(row["seals"].get("local:legacy_local", {}).get("state") in
                             {"正确", "错误", "未识别", "失败", "待标注", "测试中"} for row in rows):
        local_keys.append("local:legacy_local")
    columns = [
        {"key": "qingtong_template", "label": "清瞳 · 印章模板"},
        {"key": "qingtong_ocr", "label": "清瞳 · 文字 OCR"},
        *[{"key": key, "label": _local_label(key.split(":", 1)[1])} for key in local_keys],
    ]
    return {"items": rows, "columns": columns, "batches": batches, "task_id": task_id}
