"""Explicit standard registration/replacement from saved receipt artifacts."""
from dataclasses import dataclass
from pathlib import Path
import math
from uuid import uuid4

from flask import abort, jsonify, request
from PIL import Image

from ..domain.seal_standards import seal_candidates, requirement_key
from ..review import prepare_review_payload


@dataclass(frozen=True)
class SealStandardDependencies:
    database: object
    artifact_dir: Path
    project_result: object
    review_revision: object
    with_review_revision: object


def _image_path(ctx, url):
    prefix = "/files/artifacts/"
    if not url.startswith(prefix):
        raise ValueError("该印章缺少可用的转正清理图，请重新识别后加入")
    root = ctx.artifact_dir.resolve()
    path = (root / url[len(prefix):]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("印章处理图已不存在，请重新识别后加入")
    return path


def list_standards(ctx):
    return jsonify(items=ctx.database.list_seal_standards(request.args.get("search", "")))


def standard_sources(ctx, standard_id):
    standard = next((s for s in ctx.database.list_seal_standards() if s["id"] == standard_id), None)
    if standard is None:
        abort(404)
    sources = []
    for stored in ctx.database.seal_standard_sources(standard["requirement"]):
        current = ctx.project_result(stored)
        if requirement_key((current.get("fields") or {}).get("签章要求")) != standard["requirement_key"]:
            continue
        candidates = seal_candidates(current)
        if candidates:
            sources.append({"id": current["id"], "filename": current["filename"],
                            "created_at": current["created_at"], "candidates": candidates,
                            "review_revision": ctx.review_revision(current)})
    return jsonify(standard=standard, sources=sources)


def register_standard(ctx, result_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="请选择需要保存的标准章"), 400
    rotation = payload.get("rotation", 0)
    if isinstance(rotation, bool) or not isinstance(rotation, (float, int)) or not math.isfinite(rotation) or abs(rotation) > 180:
        return jsonify(error="旋转角度须在 -180 到 180 度之间"), 400
    destination = None
    committed = False
    try:
        with ctx.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = ctx.database.get_result(result_id, _connection=connection)
            current = ctx.project_result(stored, _connection=connection)
            if payload.get("review_revision") != ctx.review_revision(current):
                return jsonify(error="回单或标准章已更新，请刷新后重新选择", code="review_revision_conflict"), 409
            requirement = str((current.get("fields") or {}).get("签章要求") or "").strip()
            if not requirement_key(requirement):
                raise ValueError("请先在复核页补全并保存签章要求")
            candidate = next((c for c in seal_candidates(current) if c["id"] == payload.get("candidate_id")), None)
            if candidate is None or not candidate["can_register"]:
                raise ValueError("请选择有完整章色处理图、形状和颜色的客户印章")
            previous = ctx.database.get_seal_standard(requirement, _connection=connection)
            expected_revision = previous["revision"] if previous else 0
            if type(payload.get("standard_revision")) is not int or payload["standard_revision"] != expected_revision:
                return jsonify(error="标准章已变化，请刷新后明确选择加入或替换", code="standard_revision_conflict"), 409
            source = _image_path(ctx, candidate["image_url"])
            directory = ctx.database.path.parent / "seal-standards"
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{uuid4().hex}.png"
            with Image.open(source) as image:
                image = image.convert("RGB")
                if rotation:
                    image = image.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
                image.save(destination, format="PNG")
            standard = ctx.database.save_seal_standard({
                "requirement": requirement, "image_name": destination.name,
                "recognized": candidate["recognized"], "shape": candidate["shape"], "color": candidate["color"],
                "source_result_id": result_id, "source_filename": current["filename"], "candidate_id": candidate["id"],
            }, expected_revision=expected_revision, _connection=connection)
            # Refresh pending receipts in the same transaction so queue/list
            # summaries agree with the new reference. Confirmed reviews keep
            # their historical human conclusions.
            for pending in ctx.database.seal_standard_sources(requirement, limit=None,
                    pending_only=True, _connection=connection):
                if pending.get("page_role") == "continuation":
                    continue
                updated = ctx.project_result(pending, _connection=connection)
                if requirement_key((updated.get("fields") or {}).get("签章要求")) != requirement_key(requirement):
                    continue
                saved = prepare_review_payload(pending, updated, review_status="待复核",
                    final_result=updated.get("overall", "需人工复核"), note=pending.get("human_note", ""),
                    error_type=pending.get("error_type", ""))
                action = ("替换标准章" if previous else "加入印章中心") if pending["id"] == result_id else "标准章更新后重新核对"
                ctx.database.review_result(pending["id"], result=saved, review_status="待复核",
                    final_result=updated.get("overall", "需人工复核"), note=pending.get("human_note", ""),
                    error_type=pending.get("error_type", ""), action=action, _connection=connection)
        committed = True
        # Standards own their PNGs; deleting/expiring a receipt cannot remove
        # them. Keep prior revision files for existing audit snapshots.
        updated = ctx.project_result(ctx.database.get_result(result_id))
        return jsonify(standard=standard, result=ctx.with_review_revision(updated))
    except KeyError:
        abort(404)
    except (ValueError, OSError) as exc:
        return jsonify(error=str(exc)), 400
    finally:
        if destination and not committed:
            destination.unlink(missing_ok=True)
