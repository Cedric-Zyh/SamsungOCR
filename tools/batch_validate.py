from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from receipt_ocr.analyzer import ReceiptAnalyzer
from receipt_ocr.analyzer import decide_overall
from receipt_ocr.database import Database, now_iso
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.seal_reference import SealReferenceMatcher


CONSENSUS_REFERENCE_ROUTES = {
    "multi_reference_consensus",
    "high_purity_multi_reference_consensus",
    "chromatic_crop_multi_reference_consensus",
    "color_mask_multi_reference_consensus",
    "strong_prefix_color_mask_multi_reference_consensus",
}


def _apply_visual_reference(result: dict, matcher: SealReferenceMatcher) -> dict:
    """Mirror the production reference matcher in command-line validation."""
    evidence = matcher.match(result)
    if "reference_filename" not in evidence:
        return result
    seal_check = result.get("seal_check") or {}
    seal_check["visual_reference_match"] = evidence
    route = str(evidence.get("route") or "")
    candidate_index = int(
        evidence.get("consensus_candidate_index", -1)
        if route in CONSENSUS_REFERENCE_ROUTES
        else evidence.get("candidate_index", -1)
    )
    for artifact in (result.get("processing_artifacts") or {}).get("seals") or []:
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
        "message": "OCR 文字不完整，但章面与同签章要求的人工真值阳性参考章形成可靠一致",
        "score": round(max(float(seal_check.get("score", 0)), confidence), 3),
        "confidence": confidence,
        "reliable": True,
        "match_basis": (
            "人工真值参考章 + 整体彩色墨迹多参考一致"
            if route == "color_mask_multi_reference_consensus"
            else "人工真值参考章 + 强文字前缀/章色多参考一致"
            if route == "strong_prefix_color_mask_multi_reference_consensus"
            else "人工真值参考章 + 本地严格视觉一致"
        ),
        "backend": str(seal_check.get("backend") or "本地 OCR")
        + " + 本地人工真值参考章",
    })
    result["seal_check"] = seal_check
    reasons = [
        reason for reason in result.get("review_reasons", [])
        if reason != "印章内容无法可靠判断"
    ]
    result["review_reasons"] = reasons
    overall = decide_overall(result.get("date_check") or {}, seal_check, reasons)
    result.update(
        overall=overall,
        final_result=overall,
        review_status="待复核" if overall == "需人工复核" else "无需复核",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="批量验证数据目录中的回单")
    parser.add_argument("--data-dir", default="数据")
    parser.add_argument("--output", default="")
    parser.add_argument("--previews", default="tmp/validation-previews")
    parser.add_argument("--artifacts", default="tmp/validation-artifacts")
    parser.add_argument("--backend", default="paddle_v6")
    parser.add_argument("--filename", action="append", default=[], help="只验证指定文件名，可重复")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少张，0 表示不限制")
    parser.add_argument("--save-db", action="store_true", help="同时保存到本地任务、结果和复核数据库")
    parser.add_argument("--storage", default="storage", help="--save-db 使用的存储目录")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    preview_dir = Path(args.previews)
    artifact_root = Path(args.artifacts)
    preview_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    analyzer = ReceiptAnalyzer()
    results = []
    images = sorted(
        path for path in data_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    )
    if args.filename:
        wanted = set(args.filename)
        images = [path for path in images if path.name in wanted]
        missing = sorted(wanted - {path.name for path in images})
        if missing:
            parser.error("找不到文件：" + "、".join(missing))
    if args.limit > 0:
        images = images[:args.limit]
    if not images:
        parser.error("没有找到待验证图片")
    database = None
    reference_matcher = None
    task_id = ""
    storage_root = Path(args.storage)
    if args.save_db:
        database = Database(storage_root / "results.db")
        database.initialize()
        database.enforce_uncertain_review_queue()
        reference_matcher = SealReferenceMatcher(storage_root / "artifacts")
        truth_path = data_dir / "ground_truth.json"
        if truth_path.is_file():
            reference_matcher.refresh(
                database, load_ground_truth(truth_path)
            )
        task_id = uuid.uuid4().hex
        database.create_task(
            task_id, f"新增样单验证 · {args.backend}", len(images), args.backend
        )
        (storage_root / "previews").mkdir(parents=True, exist_ok=True)
        (storage_root / "artifacts").mkdir(parents=True, exist_ok=True)
    for image in images:
        token = uuid.uuid4().hex
        try:
            if args.save_db:
                preview_path = storage_root / "previews" / f"{token}.jpg"
                artifact_dir = storage_root / "artifacts" / token
                artifact_prefix = f"/files/artifacts/{token}"
            else:
                preview_path = preview_dir / f"{image.stem}.jpg"
                artifact_dir = artifact_root / token
                artifact_prefix = str(artifact_dir.resolve())
            result = analyzer.analyze(
                image, preview_path,
                artifact_dir=artifact_dir,
                artifact_url_prefix=artifact_prefix,
                ocr_backend=args.backend,
            )
            if reference_matcher is not None:
                result = _apply_visual_reference(result, reference_matcher)
            result.pop("ocr_observations", None)
            result.update(
                filename=image.name,
                preview_url=f"/files/previews/{token}.jpg" if args.save_db else str(preview_path.resolve()),
                validation_artifact_dir=str(artifact_dir.resolve()),
                created_at=now_iso(), updated_at=now_iso(),
            )
            if database:
                result["id"] = database.insert_result(
                    filename=image.name, stored_name=f"sample:{image.name}",
                    preview_name=f"{token}.jpg", task_id=task_id, result=result,
                )
                database.update_task(
                    task_id, success=True, pending_review=result["review_status"] == "待复核"
                )
            results.append(result)
            print(
                f"{image.name}: overall={result['overall']} "
                f"date={result['date_check']['status']} "
                f"seal={result['seal_check']['status']}({result['seal_check']['score']:.0%}) "
                f"time={result['processing_seconds']}s"
            )
            print(f"  required={result['date_check']['required']} actual={result['date_check']['actual']}")
            print(f"  seal={result['seal_check']['recognized']}")
        except Exception as exc:
            results.append({"filename": image.name, "error": str(exc), "overall": "识别失败"})
            if database:
                database.update_task(task_id, success=False, pending_review=True)
            print(f"{image.name}: 识别失败：{exc}")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "backend": args.backend,
            "total": len(images),
            "succeeded": sum(1 for item in results if not item.get("error")),
            "failed": sum(1 for item in results if item.get("error")),
            "task_id": task_id,
            "results": results,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
