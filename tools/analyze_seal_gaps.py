from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.analyzer import _reconstruct_exact_company_stamp_from_region


def _latest_original_results(database: Path, backend: str) -> dict[str, dict]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT id,filename,original_result_json FROM results ORDER BY id"
    ).fetchall()
    connection.close()
    latest: dict[str, dict] = {}
    for result_id, filename, payload in rows:
        try:
            result = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("ocr_backend") != backend:
            continue
        result["id"] = result_id
        result["filename"] = filename
        latest[filename] = result
    return latest


def _category(seal: dict, artifacts: list[dict]) -> str:
    score = float(seal.get("score", 0) or 0)
    company_score = float(seal.get("company_score", 0) or 0)
    if not artifacts:
        return "未检测到收货章区域"
    if not str(seal.get("recognized", "")).strip():
        return "检测到章区但无文字"
    if seal.get("company_conflict"):
        return "完整近似公司名冲突"
    if score >= 0.72 and company_score >= 0.70:
        return "公司名较强但章类型或结构不足"
    if score >= 0.50:
        return "中等相似度证据"
    return "低相似度证据"


def _artifact_urls(artifacts: list[dict]) -> list[str]:
    keys = (
        "original_url",
        "isolated_url",
        "color_isolated_url",
        "unwrapped_url",
        "unwrapped_rotated_url",
        "color_isolated_rotations_url",
        "rotated_url",
    )
    urls = [
        str(artifact.get(key))
        for artifact in artifacts
        for key in keys
        if artifact.get(key)
    ]
    urls.extend(
        str(url)
        for artifact in artifacts
        for url in artifact.get("unwrapped_band_urls", [])
        if url
    )
    return urls


def build_report(database: Path, truth_path: Path, backend: str) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    summary = Counter()
    score_bands = Counter()
    requirement_types = Counter()
    rows = []
    for filename, expected in truth.items():
        result = latest.get(filename)
        if not result:
            summary["missing_result"] += 1
            continue
        seal = result.get("seal_check") or {}
        if seal.get("reliable") is True:
            summary["already_reliable"] += 1
            continue
        summary["unreliable"] += 1
        artifacts = list(
            ((result.get("processing_artifacts") or {}).get("seals") or [])
        )
        category = _category(seal, artifacts)
        summary[category] += 1
        server_audited = any(
            bool(artifact.get("server_audit_backend")) for artifact in artifacts
        )
        server_used = any(
            artifact.get("server_audit_used_for_matching") is True
            for artifact in artifacts
        )
        server_rejected = any(
            bool(artifact.get("server_audit_rejection_reason"))
            for artifact in artifacts
        )
        summary[
            "已执行 Server 章色复核" if server_audited else "未执行 Server 章色复核"
        ] += 1
        if server_used:
            summary["Server 证据已参与匹配"] += 1
        if server_rejected:
            summary["Server 因公司名冲突仅供审计"] += 1
        exact_reconstructions = []
        for artifact in artifacts:
            audit_texts = [
                text.strip()
                for text in str(artifact.get("server_audit_text", "")).split("|")
                if text.strip()
            ]
            reconstructed = _reconstruct_exact_company_stamp_from_region(
                str(seal.get("requirement", "")), audit_texts
            )
            if reconstructed:
                exact_reconstructions.append(reconstructed)
        exact_reconstructions = list(dict.fromkeys(exact_reconstructions))
        safe_exact_reconstruction = bool(
            exact_reconstructions and not seal.get("company_conflict")
        )
        if safe_exact_reconstruction:
            summary["同章区完整公司与章型可安全重组"] += 1
        elif exact_reconstructions:
            summary["同章区片段齐全但公司冲突阻断"] += 1
        score = float(seal.get("score", 0) or 0)
        score_bands[f"{int(score * 10) / 10:.1f}"] += 1
        requirement = str(seal.get("requirement", ""))
        if "有限公司" in requirement:
            requirement_types["公司类"] += 1
        if any(token in requirement for token in ("专用章", "收货章", "仓储部")):
            requirement_types["明确章类型"] += 1
        if any(token in requirement for token in ("维修中心", "服务中心")):
            requirement_types["服务中心类"] += 1
        if any(char.isdigit() for char in requirement):
            requirement_types["含业务编号"] += 1
        rows.append({
            "filename": filename,
            "result_id": result.get("id"),
            "truth_should_match": bool(expected.get("seal_should_match")),
            "category": category,
            "requirement": requirement,
            "recognized": str(seal.get("recognized", "")),
            "status": str(seal.get("status", "")),
            "score": score,
            "company_score": float(seal.get("company_score", 0) or 0),
            "company_conflict": bool(seal.get("company_conflict")),
            "message": str(seal.get("message", "")),
            "region_count": len(artifacts),
            "shapes": sorted({
                str(artifact.get("shape", ""))
                for artifact in artifacts
                if artifact.get("shape")
            }),
            "server_audited": server_audited,
            "server_used_for_matching": server_used,
            "server_rejected": server_rejected,
            "server_rejection_reasons": sorted({
                str(artifact.get("server_audit_rejection_reason", ""))
                for artifact in artifacts
                if artifact.get("server_audit_rejection_reason")
            }),
            "exact_company_type_reconstructions": exact_reconstructions,
            "safe_exact_company_type_reconstruction": (
                safe_exact_reconstruction
            ),
            "artifact_urls": _artifact_urls(artifacts),
        })
    rows.sort(key=lambda row: (
        row["category"] != "公司名较强但章类型或结构不足",
        row["server_audited"],
        -row["score"],
        row["filename"],
    ))
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": dict(summary),
        "unreliable_score_bands": dict(sorted(score_bands.items())),
        "unreliable_requirement_types": dict(requirement_types),
        "samples": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="分析最新机器原始结果中未可靠印章的证据缺口"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database, args.truth, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
