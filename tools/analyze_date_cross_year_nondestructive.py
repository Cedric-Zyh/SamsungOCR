from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _cross_year_nondestructive_consensus_from_artifacts,
)
from tools.analyze_date_gaps import _latest_original_results


def select_saved_candidate(result: dict) -> dict:
    """Audit the cross-year non-destructive route without using truth."""
    date_check = result.get("date_check") or {}
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    marker = next((
        item for item in artifacts
        if item.get("date_cross_year_nondestructive_candidate")
    ), None)
    if date_check.get("reliable") is True and marker is None:
        return {
            "selected": False,
            "reason": "当前日期已由既有路线可靠确认",
        }
    consensus = _cross_year_nondestructive_consensus_from_artifacts(
        artifacts, str(date_check.get("required") or "")
    )
    if consensus is None:
        return {
            "selected": False,
            "reason": "未形成跨年非破坏性 Mobile/Server 几何共识",
        }
    candidate = consensus["date"].isoformat()
    confirmed = bool(marker) and str(
        marker.get("date_cross_year_nondestructive_candidate") or ""
    ) == candidate
    artifact_urls = {}
    for item in artifacts:
        if item.get("variant") not in {"紧凑区域", "宽区域"}:
            continue
        prefix = "tight" if item.get("variant") == "紧凑区域" else "wide"
        for key in (
            "date_line_original_url", "date_line_color_clean_url",
            "date_line_table_clean_url",
        ):
            artifact_urls[f"{prefix}_{key}"] = item.get(key, "")
    return {
        "selected": True,
        "candidate": candidate,
        "confirmed": confirmed,
        "support": consensus["support"],
        "artifact_urls": artifact_urls,
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
        expected_date = str(expected.get("actual_date") or "")
        samples.append({
            "filename": filename,
            "truth": expected_date,
            "correct": audit["candidate"] == expected_date,
            **audit,
        })
    confirmed = [item for item in samples if item.get("confirmed")]
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "selected": len(samples),
            "selected_correct": sum(bool(item["correct"]) for item in samples),
            "selected_false_accepts": sum(
                not bool(item["correct"]) for item in samples
            ),
            "production_confirmed": len(confirmed),
            "production_confirmed_correct": sum(
                bool(item["correct"]) for item in confirmed
            ),
            "production_false_accepts": sum(
                not bool(item["correct"]) for item in confirmed
            ),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计跨年非破坏性原图与去章色双几何共识"
    )
    parser.add_argument(
        "--database", type=Path, default=Path("storage/results.db")
    )
    parser.add_argument(
        "--truth", type=Path, default=Path("数据/ground_truth.json")
    )
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
