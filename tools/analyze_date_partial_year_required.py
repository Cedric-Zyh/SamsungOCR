from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _same_geometry_partial_year_required_consensus_from_artifacts,
)
from tools.analyze_date_gaps import _latest_original_results


def select_saved_candidate(result: dict) -> dict:
    """Audit the three-digit-year route without consulting truth data."""
    fields = result.get("fields") or {}
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    consensus = _same_geometry_partial_year_required_consensus_from_artifacts(
        artifacts,
        str(fields.get("要求到货") or ""),
        str(fields.get("制单日期") or ""),
        str(fields.get("运单号") or ""),
    )
    if consensus is None:
        return {
            "selected": False,
            "reason": "未形成宽裁双模型残缺年份共识或业务年份不一致",
        }
    candidate = consensus["date"].isoformat()
    markers = {
        str(item.get("date_partial_year_required_candidate") or "")
        for item in artifacts
        if item.get("date_partial_year_required_candidate")
    }
    return {
        "selected": True,
        "candidate": candidate,
        "production_confirmed": markers == {candidate},
        "support": consensus["support"],
    }


def build_report(database: Path, truth_path: Path, backend: str) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    samples = []
    for filename, expected in truth.items():
        result = latest.get(filename)
        if result is None:
            continue
        audit = select_saved_candidate(result)
        if not audit.get("selected"):
            continue
        truth_date = str(expected.get("actual_date") or "")
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": audit["candidate"] == truth_date,
            **audit,
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "selected": len(samples),
            "correct": sum(bool(item["correct"]) for item in samples),
            "false_accepts": sum(not bool(item["correct"]) for item in samples),
            "production_confirmed": sum(
                bool(item["production_confirmed"]) for item in samples
            ),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计宽裁双模型三位年份与三项业务年份共识"
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
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
