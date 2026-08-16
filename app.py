from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from receipt_ocr.analyzer import ReceiptAnalyzer, decide_overall
from receipt_ocr.database import Database, now_iso
from receipt_ocr.evaluation import (
    build_ground_truth_entry,
    evaluate_backends,
    evaluate_results,
    load_ground_truth,
    operational_report,
    save_ground_truth_entry,
)
from receipt_ocr.ocr_backends import backend_catalog, backend_label, default_backend, resolve_backend
from receipt_ocr.pagination import merge_paginated_results
from receipt_ocr.parser import (
    LOW_CONFIDENCE_THRESHOLD,
    compare_dates,
    compare_seal_text,
    parse_date,
    product_table_text,
)
from receipt_ocr.seal_reference import SealReferenceMatcher
from receipt_ocr.seal_api import (
    SEAL_RECOGNITION_MODES,
    resolve_seal_recognition_mode,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "数据"
STORAGE_DIR = BASE_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
PREVIEW_DIR = STORAGE_DIR / "previews"
ARTIFACT_DIR = STORAGE_DIR / "artifacts"
EXPORT_DIR = STORAGE_DIR / "exports"
DATABASE_PATH = STORAGE_DIR / "results.db"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.json"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_BUNDLED_NODE = Path(
    "/Users/zhuyihao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
)


def resolve_node_executable() -> Path | None:
    """Resolve Node without embedding a macOS-only path on Windows."""
    configured = os.getenv("WORKSPACE_NODE", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        return candidate.resolve() if candidate.is_file() else None
    discovered = shutil.which("node")
    if discovered:
        return Path(discovered).resolve()
    return _BUNDLED_NODE if _BUNDLED_NODE.is_file() else None


def export_subprocess_environment() -> dict[str, str]:
    """Keep Paddle's OpenMP workaround out of artifact-tool's runtime."""
    environment = os.environ.copy()
    environment.pop("KMP_USE_SHM", None)
    return environment


def _export_machine_scope(
    all_machine_results: list[dict],
    exported_results: list[dict],
    requested_backend: str,
) -> list[dict]:
    """Match export-summary accuracy to the exact rows in the workbook."""
    result_ids = {
        int(item.get("id") or 0) for item in exported_results
        if int(item.get("id") or 0) > 0
    }
    filenames = {
        str(item.get("filename") or "") for item in exported_results
        if str(item.get("filename") or "")
    }
    scoped = [
        item for item in all_machine_results
        if (not requested_backend or item.get("ocr_backend") == requested_backend)
        and (
            int(item.get("id") or 0) in result_ids
            if result_ids else str(item.get("filename") or "") in filenames
        )
    ]
    return scoped


NODE_EXECUTABLE = resolve_node_executable()

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=500 * 1024 * 1024, JSON_AS_ASCII=False)
analyzer = ReceiptAnalyzer()
database = Database(DATABASE_PATH)
seal_reference_matcher = SealReferenceMatcher(ARTIFACT_DIR)


def initialize() -> None:
    for directory in (UPLOAD_DIR, PREVIEW_DIR, ARTIFACT_DIR, EXPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    database.initialize()
    database.recover_interrupted_tasks()
    database.enforce_uncertain_review_queue()
    seal_reference_matcher.refresh(
        database, load_ground_truth(GROUND_TRUTH_PATH)
    )


def _apply_visual_seal_reference(result: dict) -> dict:
    """Promote only a broad match to an exact human-truth seal reference."""
    evidence = seal_reference_matcher.match(result)
    if "reference_filename" not in evidence:
        return result
    seal_check = result.get("seal_check") or {}
    seal_check["visual_reference_match"] = evidence
    route = str(evidence.get("route") or "")
    consensus_routes = {
        "multi_reference_consensus",
        "high_purity_multi_reference_consensus",
        "chromatic_crop_multi_reference_consensus",
    }
    candidate_index = int(
        evidence.get("consensus_candidate_index", -1)
        if route in consensus_routes
        else evidence.get("candidate_index", -1)
    )
    for artifact in result.get("processing_artifacts", {}).get("seals", []):
        if int(artifact.get("index", -2)) == candidate_index:
            artifact["visual_reference_match"] = evidence
            break
    if not evidence.get("accepted"):
        return result

    confidence = float(evidence.get("confidence", 0.90))
    seal_check.update({
        "ocr_only_status": seal_check.get("status", ""),
        "ocr_only_reliable": bool(seal_check.get("reliable")),
        "ocr_only_score": float(seal_check.get("score", 0)),
        "status": "匹配",
        "message": (
            "OCR 文字不完整，但章面与同签章要求的人工真值阳性参考章"
            + (
                "在两份独立样单中形成一致几何证据"
                if route in consensus_routes
                else "的彩色墨迹形成整体几何一致"
                if route == "color_mask_geometry"
                else "同时形成整体彩色墨迹与大面积局部几何一致"
                if route == "color_mask_sift_geometry"
                else "去除稀疏彩色扫描噪点后形成高纯度大面积几何一致"
                if route == "trimmed_chromatic_single_reference"
                else "形成大面积几何一致"
            )
        ),
        "score": round(max(float(seal_check.get("score", 0)), confidence), 3),
        "confidence": confidence,
        "reliable": True,
        "match_basis": (
            "人工真值参考章 + SIFT/RANSAC 多参考一致"
            if route == "multi_reference_consensus"
            else "人工真值参考章 + SIFT/RANSAC 多参考高纯度一致"
            if route == "high_purity_multi_reference_consensus"
            else "人工真值参考章 + 章色稳健裁剪/SIFT 多参考一致"
            if route == "chromatic_crop_multi_reference_consensus"
            else "人工真值参考章 + 彩色墨迹整体几何一致"
            if route == "color_mask_geometry"
            else "人工真值参考章 + 彩色墨迹/SIFT 联合几何一致"
            if route == "color_mask_sift_geometry"
            else "人工真值参考章 + 稀疏章色噪点裁剪/SIFT 高纯度一致"
            if route == "trimmed_chromatic_single_reference"
            else "人工真值参考章 + SIFT/RANSAC 高支持度微覆盖抖动"
            if route == "high_support_minor_coverage"
            else "人工真值参考章 + SIFT/RANSAC 超高支持度局部覆盖"
            if route == "ultra_support_partial_coverage"
            else "人工真值参考章 + SIFT/RANSAC 高内点率几何一致"
            if route == "high_ratio_single_reference"
            else "人工真值参考章 + SIFT/RANSAC 大面积几何一致"
        ),
        "backend": (
            str(seal_check.get("backend") or "本地 OCR")
            + " + 本地人工真值参考章"
        ),
    })
    result["seal_check"] = seal_check
    reasons = [
        reason for reason in result.get("review_reasons", [])
        if reason != "印章内容无法可靠判断"
    ]
    result["review_reasons"] = reasons
    overall = decide_overall(
        result.get("date_check") or {}, seal_check, reasons
    )
    result["overall"] = overall
    result["final_result"] = overall
    result["review_status"] = (
        "待复核" if overall == "需人工复核" else "无需复核"
    )
    return result


@app.get("/")
def index():
    # “一键测试”使用已有人工标注的基准集；目录中的其余新增图片仍可通过
    # “选择文件夹”批量导入，避免把数百张未标注图片误称为 6 张测试样单。
    samples = [name for name in load_ground_truth(GROUND_TRUTH_PATH) if (DATA_DIR / name).is_file()]
    return render_template(
        "index.html",
        samples=samples,
        seal_api_enabled=analyzer.seal_api.enabled,
        python_path=sys.executable,
        default_ocr_backend=default_backend(),
        ocr_backend_catalog=backend_catalog(),
        seal_recognition_modes=SEAL_RECOGNITION_MODES,
    )


@app.get("/api/ocr-backends")
def ocr_backends():
    selected = default_backend()
    return jsonify({
        "default": selected,
        "backends": backend_catalog(),
    })


@app.post("/api/tasks")
def create_task():
    payload = request.get_json(silent=True) or {}
    total = int(payload.get("total", 0))
    if total <= 0 or total > 5000:
        return jsonify({"error": "批量任务数量必须在 1 至 5000 之间"}), 400
    try:
        ocr_backend = resolve_backend(str(payload.get("ocr_backend") or "auto"))
        seal_recognition_mode = resolve_seal_recognition_mode(
            payload.get("seal_recognition_mode")
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    task_id = uuid.uuid4().hex
    return jsonify(database.create_task(
        task_id, str(payload.get("name") or "批量识别"), total, ocr_backend,
        seal_recognition_mode,
    ))


@app.get("/api/tasks/<task_id>")
def get_task(task_id: str):
    try:
        return jsonify(database.get_task(task_id))
    except KeyError:
        abort(404)


@app.post("/api/analyze")
def analyze_upload():
    task_id = request.form.get("task_id", "").strip()
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
            ocr_backend = resolve_backend(request.form.get("ocr_backend", "auto"))
            seal_recognition_mode = resolve_seal_recognition_mode(
                request.form.get("seal_recognition_mode")
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        task_id = uuid.uuid4().hex
        database.create_task(
            task_id, original_name, 1, ocr_backend, seal_recognition_mode
        )

    token = uuid.uuid4().hex
    preview_name = f"{token}.jpg"
    artifacts = ARTIFACT_DIR / token
    try:
        result = analyzer.analyze(
            source,
            PREVIEW_DIR / preview_name,
            artifact_dir=artifacts,
            artifact_url_prefix=f"/files/artifacts/{token}",
            ocr_backend=ocr_backend,
            seal_recognition_mode=seal_recognition_mode,
        )
        result.update(
            filename=original_name,
            preview_url=f"/files/previews/{preview_name}",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        result = _apply_visual_seal_reference(result)
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


@app.get("/api/results")
def list_results():
    limit = min(max(request.args.get("limit", 200, type=int), 1), 2000)
    filters = {key: request.args.get(key, "") for key in (
        "filename", "order_id", "customer", "date", "overall", "review_status",
        "task_id", "ocr_backend",
    )}
    return jsonify(merge_paginated_results(
        database.list_results(
            limit=limit,
            filters=filters,
            latest_by_filename=True,
        )
    ))


@app.get("/api/history")
def list_result_history():
    limit = min(max(request.args.get("limit", 200, type=int), 1), 2000)
    filters = {key: request.args.get(key, "") for key in (
        "filename", "order_id", "customer", "date", "overall", "review_status",
        "task_id", "ocr_backend",
    )}
    return jsonify(merge_paginated_results(
        database.list_results(limit=limit, filters=filters)
    ))


@app.get("/api/results/<int:result_id>")
def get_result(result_id: int):
    try:
        return jsonify(_project_paginated_result(database.get_result(result_id)))
    except KeyError:
        abort(404)


@app.patch("/api/results/<int:result_id>/review")
def review_result(result_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        stored_current = database.get_result(result_id)
    except KeyError:
        abort(404)
    current = _project_paginated_result(stored_current)
    updated = _apply_human_edits(current, payload)
    review_status = str(payload.get("review_status", "待复核"))
    final_result = str(payload.get("final_result") or updated.get("overall", "需人工复核"))
    confirmation_error = _confirmation_error(updated, review_status, final_result)
    if confirmation_error:
        return jsonify({"error": confirmation_error}), 409
    truth_entry = None
    if payload.get("save_ground_truth"):
        if review_status not in {"确认通过", "确认不通过"}:
            return jsonify({"error": "只有完成确认通过/不通过后才能保存评测真值"}), 400
        seal_should_match = payload.get("truth_seal_should_match")
        if not isinstance(seal_should_match, bool):
            return jsonify({"error": "请选择真值中的印章是否应匹配"}), 400
        try:
            truth_entry = build_ground_truth_entry(
                updated,
                seal_should_match=seal_should_match,
                date_present=bool(payload.get("truth_date_present", True)),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    try:
        result_to_store = updated
        is_paginated = bool(
            current.get("page_group", {}).get("page_count", 0) > 1
            and current.get("page_role") != "continuation"
        )
        if is_paginated:
            override = {
                key: updated[key]
                for key in (
                    "fields", "field_metadata", "product_table", "date_check",
                    "seal_check", "review_reasons", "overall",
                )
                if key in updated
            }
            override.update(
                review_status=review_status,
                final_result=final_result,
                human_note=str(payload.get("human_note", "")),
                error_type=str(payload.get("error_type", "")),
            )
            result_to_store = dict(stored_current)
            result_to_store["page_review_override"] = override
            result_to_store["overall"] = updated.get("overall", "需人工复核")
        reviewed = database.review_result(
            result_id,
            result=result_to_store,
            review_status=review_status,
            final_result=final_result,
            note=str(payload.get("human_note", "")),
            action=str(payload.get("action", "人工复核")),
            error_type=str(payload.get("error_type", "")),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if current.get("page_group", {}).get("page_count", 0) > 1:
        reviewed = _project_paginated_result(reviewed)
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
    return jsonify(reviewed)


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
    result = analyzer.analyze(
        source,
        PREVIEW_DIR / preview_name,
        artifact_dir=ARTIFACT_DIR / token,
        artifact_url_prefix=f"/files/artifacts/{token}",
        ocr_backend=ocr_backend,
        seal_recognition_mode=seal_recognition_mode,
    )
    result.update(
        filename=current["filename"], preview_url=f"/files/previews/{preview_name}",
        created_at=current["created_at"], updated_at=now_iso(),
    )
    result = _apply_visual_seal_reference(result)
    return jsonify(database.replace_after_retry(result_id, result, preview_name))


@app.post("/api/results/bulk-review")
def bulk_review():
    payload = request.get_json(silent=True) or {}
    ids = [int(item) for item in payload.get("ids", [])]
    if not ids:
        return jsonify({"error": "请选择回单"}), 400
    review_status = str(payload.get("review_status", "待复核"))
    requested_final = str(payload.get("final_result", ""))
    current_items = []
    try:
        for result_id in ids:
            current_items.append((result_id, _project_paginated_result(database.get_result(result_id))))
    except KeyError:
        return jsonify({"error": "选中的回单不存在"}), 404
    invalid = []
    for result_id, current in current_items:
        final_result = requested_final or str(current.get("overall", ""))
        error = _confirmation_error(current, review_status, final_result)
        if error:
            invalid.append({"id": result_id, "filename": current.get("filename", ""), "reason": error})
    if invalid:
        return jsonify({
            "error": "部分回单的日期或印章证据不完整，不能批量确认通过",
            "invalid": invalid,
        }), 409
    output = []
    for result_id, current in current_items:
        update = {
            "review_status": review_status,
            "final_result": requested_final or current.get("overall", ""),
            "human_note": payload.get("human_note", ""),
            "action": "批量确认",
        }
        output.append(database.review_result(
            result_id, result=current, review_status=update["review_status"],
            final_result=update["final_result"], note=update["human_note"], action=update["action"],
        ))
    return jsonify(output)


@app.post("/api/results/bulk-retry")
def bulk_retry():
    payload = request.get_json(silent=True) or {}
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
            result = analyzer.analyze(
                source, PREVIEW_DIR / preview_name,
                artifact_dir=ARTIFACT_DIR / token,
                artifact_url_prefix=f"/files/artifacts/{token}",
                ocr_backend=ocr_backend,
                seal_recognition_mode=seal_recognition_mode,
            )
            result.update(filename=current["filename"], preview_url=f"/files/previews/{preview_name}")
            result = _apply_visual_seal_reference(result)
            output.append({"id": result_id, "ok": True, "result": database.replace_after_retry(result_id, result, preview_name)})
        except Exception as exc:
            output.append({"id": result_id, "ok": False, "error": str(exc)})
    return jsonify(output)


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
    results = merge_paginated_results(database.list_results(
        limit=5000,
        filters={"ocr_backend": requested_backend},
        latest_by_filename=True,
    ))
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


@app.get("/api/export.xlsx")
def export_excel():
    filters = {key: request.args.get(key, "") for key in (
        "filename", "order_id", "customer", "date", "overall", "review_status",
        "task_id", "ocr_backend",
    )}
    results = merge_paginated_results(
        database.list_results(
            limit=5000,
            filters=filters,
            latest_by_filename=True,
        )
    )
    all_machine_results = database.list_original_results(
        limit=5000, completed_tasks_only=True
    )
    requested_backend = str(request.args.get("ocr_backend", "")).strip()
    machine_results = _export_machine_scope(
        all_machine_results, results, requested_backend
    )
    accuracy = evaluate_results(
        machine_results, load_ground_truth(GROUND_TRUTH_PATH)
    )
    accuracy["scope_backend"] = requested_backend
    accuracy["scope_backend_label"] = (
        backend_label(requested_backend) if requested_backend else "全部后端最新结果"
    )
    accuracy["scope_export_rows"] = len(results)
    report_data = operational_report(
        results,
        database.all_history(),
        accuracy,
    )
    export_id = uuid.uuid4().hex
    input_path = EXPORT_DIR / f"{export_id}.json"
    output_path = EXPORT_DIR / f"三星回单识别结果-{export_id[:8]}.xlsx"
    input_path.write_text(json.dumps({"results": results, "report": report_data}, ensure_ascii=False), encoding="utf-8")
    if NODE_EXECUTABLE is None:
        return jsonify({
            "error": "Excel 导出需要 Node.js",
            "detail": "请安装 Node.js，或通过 WORKSPACE_NODE 指定 node.exe 的完整路径",
        }), 503
    command = [str(NODE_EXECUTABLE), str(BASE_DIR / "tools" / "build_export.mjs"), str(input_path), str(output_path)]
    completed = subprocess.run(
        command,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        env=export_subprocess_environment(),
    )
    if completed.returncode != 0 or not output_path.exists():
        app.logger.error("Excel export failed: %s", completed.stderr)
        return jsonify({"error": "Excel 导出失败", "detail": completed.stderr[-1000:]}), 500
    return send_file(output_path, as_attachment=True, download_name="三星回单识别结果.xlsx")


@app.get("/files/<kind>/<path:name>")
def files(kind: str, name: str):
    directory = {"previews": PREVIEW_DIR, "uploads": UPLOAD_DIR, "artifacts": ARTIFACT_DIR}.get(kind)
    if directory is None:
        abort(404)
    return send_from_directory(directory, name)


def _confirmation_error(result: dict, review_status: str, final_result: str) -> str:
    """Reject a human 'pass' when the auditable evidence is still incomplete."""
    if review_status != "确认通过" and final_result != "通过":
        return ""
    date_check = result.get("date_check") or {}
    seal_check = result.get("seal_check") or {}
    missing = []
    if not (
        date_check.get("actual")
        and date_check.get("status") == "匹配"
        and date_check.get("reliable") is True
    ):
        missing.append("实际收货日期尚未可靠识别或与要求到货日期不一致")
    if not (
        seal_check.get("recognized")
        and seal_check.get("status") == "匹配"
        and seal_check.get("reliable") is True
    ):
        missing.append("印章内容尚未可靠识别或与签章要求不一致")
    if result.get("overall") != "通过" and not missing:
        missing.append("整体核验结论尚未达到通过条件")
    if not missing:
        return ""
    return "确认通过前请补全并核对：" + "；".join(missing)


def _apply_human_edits(current: dict, payload: dict) -> dict:
    fields = dict(current.get("fields", {}))
    metadata = dict(current.get("field_metadata", {}))
    for name, value in (payload.get("fields") or {}).items():
        value = str(value).strip()
        previous = fields.get(name, "")
        fields[name] = value
        if value != previous:
            meta = dict(metadata.get(name, {}))
            meta.update(original=meta.get("original", previous), value=value, confidence=1.0, low_confidence=False, source="人工复核")
            metadata[name] = meta
    current["fields"] = fields
    current["field_metadata"] = metadata

    table = current.get("product_table") or {}
    table_rows = table.get("rows") or []
    columns = set(table.get("columns") or [])
    for edit in payload.get("product_rows") or []:
        row_index = int(edit.get("row", -1))
        column = str(edit.get("column", ""))
        if row_index < 0 or row_index >= len(table_rows) or column not in columns:
            continue
        detail = table_rows[row_index]
        value = str(edit.get("value", "")).strip()
        previous = str(detail.get("values", {}).get(column, ""))
        if value == previous:
            continue
        detail.setdefault("original_values", {}).setdefault(column, previous)
        detail.setdefault("values", {})[column] = value
        detail.setdefault("confidences", {})[column] = 1.0
        detail.setdefault("sources", {})[column] = "人工复核"
        detail["low_confidence_columns"] = [
            name for name in detail.get("low_confidence_columns", []) if name != column
        ]
        scores = [score for name, score in detail["confidences"].items() if detail["values"].get(name)]
        detail["row_confidence"] = round(sum(scores) / max(1, len(scores)), 3)
    if table_rows:
        scores = [
            score for detail in table_rows for name, score in detail.get("confidences", {}).items()
            if detail.get("values", {}).get(name)
        ]
        table["confidence"] = round(sum(scores) / max(1, len(scores)), 3)
        current["product_table"] = table
        fields["商品明细原文"] = product_table_text(table)
        table_meta = dict(metadata.get("商品明细原文", {}))
        table_meta.update(
            value=fields["商品明细原文"],
            confidence=table["confidence"],
            low_confidence=table["confidence"] < LOW_CONFIDENCE_THRESHOLD,
            source="商品表格按列识别 + 人工复核",
        )
        metadata["商品明细原文"] = table_meta

    actual = str(payload.get("actual_date", current.get("date_check", {}).get("actual", ""))).strip()
    actual_date = parse_date(actual)
    date_check = compare_dates(fields.get("要求到货", ""), actual_date)
    date_check.update(confidence=1.0 if actual_date else 0.0, reliable=bool(actual_date), source="人工复核")
    current["date_check"] = date_check

    seal_text = str(payload.get("seal_text", current.get("seal_check", {}).get("recognized", ""))).strip()
    seal_check = compare_seal_text(fields.get("签章要求", ""), [seal_text] if seal_text else [])
    human_seal_match = payload.get("truth_seal_should_match")
    if isinstance(human_seal_match, bool) and seal_text:
        seal_check.update(
            status="匹配" if human_seal_match else "不匹配",
            message=(
                "人工复核确认印章与签章要求一致"
                if human_seal_match else "人工复核确认印章与签章要求不一致"
            ),
            score=1.0,
            human_confirmed_match=human_seal_match,
        )
    seal_check.update(confidence=1.0 if seal_text else 0.0, reliable=bool(seal_text), source="人工复核", backend="人工复核")
    current["seal_check"] = seal_check

    if date_check["status"] == "不匹配" or seal_check["status"] == "不匹配":
        current["overall"] = "不通过"
    elif date_check["status"] == "匹配" and seal_check["status"] == "匹配":
        current["overall"] = "通过"
    else:
        current["overall"] = "需人工复核"
    current["review_reasons"] = [] if current["overall"] != "需人工复核" else ["人工复核信息尚不完整"]
    return current


def _project_paginated_result(item: dict) -> dict:
    """Return one logical receipt when ``item`` is a paginated cover."""
    page_group = item.get("page_group") or {}
    if page_group.get("page_count", 0) <= 1 or item.get("page_role") == "continuation":
        return item
    task_id = str(item.get("task_id") or "")
    if not task_id:
        return item
    projected = merge_paginated_results(
        database.list_results(limit=5000, filters={"task_id": task_id})
    )
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


initialize()


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5001")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
