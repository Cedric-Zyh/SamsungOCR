from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.database import Database
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.analyzer import _cross_year_strict_consensus_from_artifacts
from receipt_ocr.parser import parse_date


def cross_year_consensus(result: dict) -> dict:
    required = parse_date(str((result.get("fields") or {}).get("要求到货") or ""))
    if required is None:
        return {"accepted": False, "reason": "没有要求到货日期"}
    evidence = _cross_year_strict_consensus_from_artifacts(
        (result.get("processing_artifacts") or {}).get("date") or [],
        required.isoformat(),
    )
    if evidence is None:
        return {"accepted": False, "reason": "未形成严格跨年共识"}
    return {
        "accepted": True,
        "reason": "Paddle 双模型在紧裁和宽裁均读到唯一跨年完整日期",
        "candidate": evidence["date"].isoformat(),
        "required": required.isoformat(),
        "support": evidence["support"],
        "strict_observation_count": evidence["strict_observation_count"],
    }


def build_report(database: Database, truth_path: Path, backend: str) -> dict:
    truth = load_ground_truth(truth_path)
    results = database.list_original_results(
        limit=5000, completed_tasks_only=True
    )
    latest = {}
    for result in results:
        if result.get("ocr_backend") != backend:
            continue
        latest.setdefault(str(result.get("filename") or ""), result)
    samples = []
    for filename, result in latest.items():
        evidence = cross_year_consensus(result)
        if not evidence.get("accepted"):
            continue
        expected = truth.get(filename) or {}
        truth_date = str(expected.get("actual_date") or "")
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": evidence.get("candidate") == truth_date,
            **evidence,
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "summary": {
            "accepted": len(samples),
            "correct": sum(bool(item["correct"]) for item in samples),
            "false_accepts": sum(not bool(item["correct"]) for item in samples),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计 Paddle 双模型双几何的严格跨年日期共识"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(Database(args.database), args.truth, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
