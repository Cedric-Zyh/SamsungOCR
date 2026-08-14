from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _missing_year_separator_consensus_from_artifacts,
)
from tools.analyze_date_gaps import _latest_original_results


def select_saved_candidate(result: dict) -> dict:
    """Audit the missing-``年`` route without consulting ground truth."""
    date_check = result.get("date_check") or {}
    required = str(date_check.get("required") or "")
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    has_production_marker = any(
        item.get("date_missing_year_separator_candidate")
        for item in artifacts
    )
    if date_check.get("reliable") is True and not has_production_marker:
        return {
            "selected": False,
            "reason": "当前日期已由既有路线可靠确认",
        }
    consensus = _missing_year_separator_consensus_from_artifacts(
        artifacts, required
    )
    if consensus is None:
        return {
            "selected": False,
            "reason": "未形成 Mobile 漏“年”自包含日期与 Server 严格日期共识",
        }
    candidate = consensus["date"].isoformat()
    production_markers = {
        str(item.get("date_missing_year_separator_candidate") or "")
        for item in artifacts
        if item.get("date_missing_year_separator_candidate")
    }
    day_slots = [
        item for item in artifacts
        if item.get("date_slot_day_inner_reliable") is True
        and str(item.get("date_slot_day_inner_candidate") or "")
        == str(consensus["date"].day)
    ]
    confirmed = production_markers == {candidate} and bool(day_slots)
    return {
        "selected": True,
        "candidate": candidate,
        "confirmed": confirmed,
        "reason": (
            "生产日槽四单元已确认"
            if confirmed else "保存证据通过预筛，尚无生产日槽四单元确认"
        ),
        "mobile": consensus["mobile"],
        "server": consensus["server"],
        "other_dates": consensus["other_dates"],
        "day_slot_ocr": (
            day_slots[0].get("date_slot_day_inner_ocr_variants", [])
            if day_slots else []
        ),
        "artifact_urls": ({
            key: day_slots[0].get(key, "")
            for key in (
                "date_slot_day_inner_original_url",
                "date_slot_day_inner_processed_url",
                "date_slot_day_inner_line_clean_url",
            )
        } if day_slots else {}),
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
        candidate = str(audit.get("candidate") or "")
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": candidate == truth_date,
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
        description="审计漏印刷“年”的完整日期与去单位日槽共识"
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
