from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _single_server_strict_truncated_day_candidate,
)
from tools.analyze_date_gaps import _latest_original_results


def select_saved_candidate(result: dict) -> dict:
    """Audit the unique-Server-date/truncated-day evidence prefilter."""
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    candidate = _single_server_strict_truncated_day_candidate(artifacts)
    if candidate is None:
        return {
            "selected": False,
            "reason": "无唯一 Server 完整日期或紧宽漏一位日证据不足",
        }
    value = candidate.isoformat()
    production_markers = {
        str(item.get("date_adaptive_day_slot_candidate") or "")
        for item in artifacts
        if item.get("date_adaptive_day_slot_candidate")
    }
    tight_artifact = next(
        (item for item in artifacts if item.get("variant") == "紧凑区域"),
        {},
    )
    variants = [
        item
        for item in tight_artifact.get("date_slot_ocr_variants") or []
        if item.get("slot") == "自适应日数字槽"
    ]
    return {
        "selected": True,
        "candidate": value,
        "production_confirmed": production_markers == {value},
        "adaptive_day_variants": variants,
        "original_url": tight_artifact.get(
            "date_slot_adaptive_day_original_url", ""
        ),
        "processed_url": tight_artifact.get(
            "date_slot_adaptive_day_processed_url", ""
        ),
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
        description="审计唯一 Server 完整日期与自适应日位共识"
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
