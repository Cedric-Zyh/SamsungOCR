from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.parser import parse_date, parse_receipt_date
from tools.analyze_date_gaps import _latest_original_results, _observations


PRIMARY_VARIANTS = {"紧凑区域", "宽区域"}


def select_saved_candidate(result: dict) -> dict:
    """Audit repeated strict Server evidence without looking at truth."""
    date_check = result.get("date_check") or {}
    required = parse_date(str(date_check.get("required") or ""))
    if date_check.get("reliable") is True or required is None:
        return {"accepted": False, "reason": "当前日期已可靠或要求日期无效"}

    parsed_rows: list[dict] = []
    for row in _observations(result):
        if row.get("variant") not in PRIMARY_VARIANTS:
            continue
        text = str(row.get("text") or "")
        strict = parse_date(text)
        parsed = strict or parse_receipt_date(text, required)
        if parsed is None:
            continue
        parsed_rows.append({**row, "parsed": parsed, "strict": strict is not None})

    all_dates = {row["parsed"] for row in parsed_rows}
    if all_dates != {required}:
        return {
            "accepted": False,
            "reason": "存在其他日期或没有可解析日期",
            "dates": sorted(value.isoformat() for value in all_dates),
        }
    server_cells: dict[str, set[str]] = defaultdict(set)
    evidence = []
    for row in parsed_rows:
        if row.get("engine") != "server" or not row["strict"]:
            continue
        variant = str(row["variant"])
        preprocessing = str(row["preprocessing"])
        server_cells[variant].add(preprocessing)
        evidence.append({
            "text": row["text"],
            "variant": variant,
            "preprocessing": preprocessing,
        })
    qualifying = {
        variant: sorted(labels)
        for variant, labels in server_cells.items()
        if len(labels) >= 3
    }
    if not qualifying:
        return {
            "accepted": False,
            "reason": "Server 未在同一几何区域形成三种预处理重复",
            "server_cells": {
                variant: len(labels) for variant, labels in server_cells.items()
            },
        }
    return {
        "accepted": True,
        "candidate": required.isoformat(),
        "reason": "唯一日期等于要求日期，Server 在同一几何区域三种预处理严格重复",
        "qualifying_variants": qualifying,
        "evidence": evidence,
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
        if not audit.get("accepted"):
            continue
        truth_date = str(expected.get("actual_date") or "")
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": audit.get("candidate") == truth_date,
            **audit,
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "accepted": len(samples),
            "correct": sum(bool(row["correct"]) for row in samples),
            "false_accepts": sum(not bool(row["correct"]) for row in samples),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计 Server 同几何区域三种预处理完整日期重复规则"
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
