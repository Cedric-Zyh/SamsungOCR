from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _partial_year_day_before_audit_from_artifacts,
)
from tools.analyze_date_gaps import _latest_original_results


def select_saved_candidate(result: dict) -> dict:
    """Audit the four-cell damaged-year route without using truth."""
    date_check = result.get("date_check") or {}
    fields = result.get("fields") or {}
    artifacts = (result.get("processing_artifacts") or {}).get("date") or []
    evidence = _partial_year_day_before_audit_from_artifacts(
        artifacts,
        str(date_check.get("required") or fields.get("要求到货") or ""),
        str(fields.get("制单日期") or ""),
    )
    if evidence is None:
        return {
            "selected": False,
            "reason": "未形成残缺年份 Mobile/Server 双模型双几何前一日共识",
        }
    candidate = evidence["date"].isoformat()
    markers = {
        str(item.get("date_partial_year_day_before_candidate") or "")
        for item in artifacts
        if item.get("date_partial_year_day_before_candidate")
    }
    confirmed = (
        markers == {candidate}
        and str(date_check.get("actual") or "") == candidate
        and date_check.get("reliable") is False
        and bool(date_check.get("partial_year_day_before_audit"))
    )
    return {
        "selected": True,
        "candidate": candidate,
        "confirmed_review_suggestion": confirmed,
        "reason": (
            "生产流程已保存 46% 低置信度人工建议"
            if confirmed else "四单元预筛通过，尚未写入生产人工建议"
        ),
        "support": evidence["support"],
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
    confirmed = [
        item for item in samples
        if item.get("confirmed_review_suggestion")
    ]
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "prefilter_selected": len(samples),
            "prefilter_correct": sum(bool(item["correct"]) for item in samples),
            "prefilter_false_candidates": sum(
                not bool(item["correct"]) for item in samples
            ),
            "review_suggestions": len(confirmed),
            "review_suggestions_correct": sum(
                bool(item["correct"]) for item in confirmed
            ),
            "review_suggestion_false_candidates": sum(
                not bool(item["correct"]) for item in confirmed
            ),
            "automatic_decisions": 0,
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计残缺年份四单元前一日低置信度人工建议"
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
