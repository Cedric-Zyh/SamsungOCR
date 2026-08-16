from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from receipt_ocr.analyzer import (
    _unanimous_month_day_business_year_consensus_from_artifacts,
)
from receipt_ocr.evaluation import load_ground_truth


def _latest_hybrid_results(database: Path) -> dict[str, dict]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT filename,original_result_json FROM results ORDER BY id"
    ).fetchall()
    connection.close()
    latest: dict[str, dict] = {}
    for filename, payload in rows:
        try:
            result = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if result.get("ocr_backend") == "hybrid":
            latest[str(filename)] = result
    return latest


def build_report(database: Path, truth_path: Path) -> dict:
    truth = load_ground_truth(truth_path)
    latest = _latest_hybrid_results(database)
    audited = []
    accepted = []
    for filename, expected_row in truth.items():
        result = latest.get(filename)
        if result is None or (result.get("date_check") or {}).get(
            "reliable"
        ) is True:
            continue
        artifacts = (result.get("processing_artifacts") or {}).get("date", [])
        if not artifacts:
            continue
        fields = result.get("fields") or {}
        selected = _unanimous_month_day_business_year_consensus_from_artifacts(
            artifacts,
            str(fields.get("要求到货") or ""),
            str(fields.get("制单日期") or ""),
            str(fields.get("运单号") or ""),
        )
        candidate = selected["date"] if selected else None
        row = {
            "filename": filename,
            "truth": str(expected_row.get("actual_date") or ""),
            "candidate": candidate.isoformat() if candidate else "",
            "accepted": candidate is not None,
            "correct": bool(
                candidate
                and candidate.isoformat()
                == str(expected_row.get("actual_date") or "")
            ),
            "discarded_strict": (
                selected["discarded_strict"].isoformat() if selected else ""
            ),
            "support": selected["support"] if selected else [],
        }
        audited.append(row)
        if candidate is not None:
            accepted.append(row)
    false_accepts = [
        row["filename"] for row in accepted if not row["correct"]
    ]
    return {
        "backend": "hybrid",
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "unreliable_with_artifacts": len(audited),
        "accepted": len(accepted),
        "correct_accepts": sum(row["correct"] for row in accepted),
        "false_accepts": false_accepts,
        "safe": not false_accepts,
        "samples": accepted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计跨模型月日一致与三业务日期年份共识"
    )
    parser.add_argument(
        "--database", type=Path, default=Path("storage/results.db")
    )
    parser.add_argument(
        "--truth", type=Path, default=Path("数据/ground_truth.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database, args.truth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: report[key]
        for key in (
            "truth_samples", "latest_results", "unreliable_with_artifacts",
            "accepted", "correct_accepts", "false_accepts", "safe",
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
