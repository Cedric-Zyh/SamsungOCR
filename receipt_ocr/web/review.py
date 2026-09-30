"""HTTP handlers for human review and ground-truth confirmation."""

from __future__ import annotations

from flask import abort, jsonify, request

from receipt_ocr.evaluation import build_ground_truth_entry, load_ground_truth, save_ground_truth_entry
from receipt_ocr.review import prepare_review_payload


def review_result(ctx, result_id: int):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="请提供有效的复核内容"), 400
    if payload.get("actual_date_confirmed") is not None and not isinstance(payload["actual_date_confirmed"], bool):
        return jsonify(error="日期确认结果必须为布尔值"), 400
    if payload.get("signature_confirmed_match") is not None and not isinstance(payload["signature_confirmed_match"], bool):
        return jsonify(error="签名确认结果必须为匹配或不匹配"), 400
    seal_confirmation = payload.get("seal_confirmed_match")
    if seal_confirmation is not None and not isinstance(seal_confirmation, bool):
        return jsonify(error="印章确认结果必须为匹配或不匹配"), 400
    if (payload.get("save_ground_truth") and isinstance(seal_confirmation, bool)
            and payload.get("truth_seal_should_match") is not seal_confirmation):
        return jsonify(error="印章真值结论与本次人工确认不一致，请核对"), 409
    try:
        with ctx.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored_current = ctx.database.get_result(result_id)
            current = ctx._project_paginated_result(stored_current)
            if "review_revision" in payload and payload["review_revision"] != ctx._review_revision(current):
                return jsonify(error="这张回单已在其他窗口或重新识别中更新，请重新打开后核对；当前编辑尚未保存。",
                               code="review_revision_conflict"), 409
            updated = ctx._apply_human_edits(current, payload)
            review_status = str(payload.get("review_status", "待复核"))
            final_result = str(payload.get("final_result") or updated.get("overall", "需人工复核"))
            confirmation_error = ctx._confirmation_error(updated, review_status, final_result)
            if confirmation_error:
                return jsonify(error=confirmation_error), 409
            truth_entry = None
            if payload.get("save_ground_truth"):
                if review_status not in {"确认通过", "确认不通过"}:
                    return jsonify(error="只有完成确认通过/不通过后才能保存评测真值"), 400
                seal_should_match = payload.get("truth_seal_should_match")
                if not isinstance(seal_should_match, bool):
                    return jsonify(error="请选择真值中的印章是否应匹配"), 400
                truth_entry = build_ground_truth_entry(
                    updated, seal_should_match=seal_should_match,
                    date_present=bool(payload.get("truth_date_present", True)),
                )
            result_to_store = prepare_review_payload(
                stored_current, updated, review_status=review_status,
                final_result=final_result, note=str(payload.get("human_note", "")),
                error_type=str(payload.get("error_type", "")),
            )
            ctx.database.review_result(
                result_id, result=result_to_store, review_status=review_status,
                final_result=final_result, note=str(payload.get("human_note", "")),
                action=str(payload.get("action", "人工复核")),
                error_type=str(payload.get("error_type", "")), _connection=connection,
            )
    except KeyError:
        abort(404)
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify(error=str(exc) if isinstance(exc, ValueError) else "复核字段格式无效，请检查输入内容"), 400
    reviewed = ctx._project_paginated_result(ctx.database.get_result(result_id))
    if truth_entry is not None:
        change = save_ground_truth_entry(ctx.GROUND_TRUTH_PATH, reviewed["filename"], truth_entry)
        history = ctx.database.record_ground_truth_change(
            filename=reviewed["filename"], result_id=result_id,
            before=change["before"], after=change["after"],
            note=str(payload.get("human_note", "")),
        )
        reviewed["ground_truth_saved"] = {
            "total": change["total"], "action": history["action"],
            "changed_at": history["changed_at"],
        }
        ctx.seal_reference_matcher.refresh(ctx.database, load_ground_truth(ctx.GROUND_TRUTH_PATH))
    return jsonify(ctx._with_review_revision(reviewed))


def bulk_review(ctx):
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not ids or len(ids) > 2000
            or any(type(item) is not int or item <= 0 for item in ids)):
        return jsonify(error="请选择有效的回单记录"), 400
    ids = list(dict.fromkeys(ids))
    review_status = str(payload.get("review_status", "待复核"))
    requested_final = str(payload.get("final_result") or "")
    try:
        with ctx.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_items = []
            invalid = []
            for result_id in ids:
                stored = ctx.database.get_result(result_id)
                current = ctx._project_paginated_result(stored)
                final_result = requested_final or str(current.get("overall", ""))
                error = ctx._confirmation_error(current, review_status, final_result)
                if error:
                    invalid.append({"id": result_id, "filename": current.get("filename", ""), "reason": error})
                current_items.append((result_id, stored, current, final_result))
            if invalid:
                return jsonify(error="部分回单的日期或印章证据不完整，不能批量确认通过", invalid=invalid), 409
            for result_id, stored, current, final_result in current_items:
                note = str(payload.get("human_note", ""))
                reviewed = prepare_review_payload(
                    stored, current, review_status=review_status,
                    final_result=final_result, note=note,
                )
                ctx.database.review_result(
                    result_id, result=reviewed, review_status=review_status,
                    final_result=final_result, note=note, action="批量确认",
                    _connection=connection,
                )
    except KeyError:
        return jsonify(error="选中的回单不存在"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify([ctx._project_paginated_result(ctx.database.get_result(result_id)) for result_id in ids])
