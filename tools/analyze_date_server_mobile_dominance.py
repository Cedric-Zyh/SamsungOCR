from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.parser import parse_date, parse_receipt_date
from tools.analyze_date_gaps import (
    _evidence_completeness,
    _latest_original_results,
    _observations,
)


PRIMARY_VARIANTS = {"紧凑区域", "宽区域"}


def select_saved_candidate(result: dict) -> dict:
    """Select a date from saved OCR evidence without consulting truth data.

    The candidate must be the only strict Server date, must have independent
    Mobile month/day support in both primary geometric crops, and may override
    at most one isolated Mobile-only interference observation.  This keeps the
    rule narrow enough to distinguish a recurring handwriting reading from a
    one-transform artefact.
    """
    date_check = result.get("date_check") or {}
    required = parse_date(str(date_check.get("required") or ""))
    if date_check.get("reliable") is True or required is None:
        return {"accepted": False, "reason": "当前日期已可靠或要求日期无效"}

    parsed_observations: list[dict] = []
    for observation in _observations(result):
        if observation.get("variant") not in PRIMARY_VARIANTS:
            continue
        text = str(observation.get("text") or "")
        parsed = parse_date(text) or parse_receipt_date(text, required)
        if parsed is None or parsed.year != required.year:
            continue
        parsed_observations.append({
            **observation,
            "parsed": parsed,
            "strict": parse_date(text) is not None,
            "completeness": _evidence_completeness(text),
        })

    strict_server_dates = {
        item["parsed"]
        for item in parsed_observations
        if item.get("engine") == "server" and item.get("strict")
    }
    if len(strict_server_dates) != 1:
        return {
            "accepted": False,
            "reason": "Server 完整日期不是唯一候选",
            "server_strict_dates": sorted(
                value.isoformat() for value in strict_server_dates
            ),
        }
    candidate = next(iter(strict_server_dates))
    if abs((candidate - required).days) > 3:
        return {
            "accepted": False,
            "reason": "候选超出要求到货日前后三天安全窗",
            "candidate": candidate.isoformat(),
            "required": required.isoformat(),
        }

    mobile_support = [
        item for item in parsed_observations
        if item.get("engine") == "mobile" and item["parsed"] == candidate
    ]
    support_variants = {item["variant"] for item in mobile_support}
    support_preprocess = {
        (item["variant"], item["preprocessing"]) for item in mobile_support
    }
    if support_variants != PRIMARY_VARIANTS or len(support_preprocess) < 4:
        return {
            "accepted": False,
            "reason": "Mobile 未在紧凑/宽区域形成四路重复支持",
            "candidate": candidate.isoformat(),
            "mobile_variants": sorted(support_variants),
            "mobile_cells": len(support_preprocess),
        }

    conflicting = [
        item for item in parsed_observations if item["parsed"] != candidate
    ]
    conflict_dates = {item["parsed"] for item in conflicting}
    conflict_cells = {
        (item["engine"], item["variant"], item["preprocessing"])
        for item in conflicting
    }
    if (
        len(conflict_dates) > 1
        or len(conflict_cells) > 1
        or any(item.get("engine") != "mobile" for item in conflicting)
        or any(item.get("strict") for item in conflicting)
    ):
        return {
            "accepted": False,
            "reason": "冲突不是单个 Mobile 非完整日期孤立观察",
            "candidate": candidate.isoformat(),
            "conflicts": sorted(value.isoformat() for value in conflict_dates),
            "conflict_cells": len(conflict_cells),
        }

    by_engine_geometry: dict[str, set[str]] = defaultdict(set)
    for item in parsed_observations:
        if item["parsed"] == candidate:
            by_engine_geometry[str(item["engine"])].add(str(item["variant"]))
    return {
        "accepted": True,
        "candidate": candidate.isoformat(),
        "reason": (
            "唯一 Server 完整日期获 Mobile 紧凑/宽区域四路支持，"
            "其余最多一个 Mobile 非完整日期孤立观察"
        ),
        "candidate_support": {
            engine: sorted(variants)
            for engine, variants in sorted(by_engine_geometry.items())
        },
        "mobile_cells": len(support_preprocess),
        "conflicts": sorted(value.isoformat() for value in conflict_dates),
        "conflict_cells": len(conflict_cells),
        "evidence": [
            {
                "text": item["text"],
                "engine": item["engine"],
                "variant": item["variant"],
                "preprocessing": item["preprocessing"],
                "strict": item["strict"],
            }
            for item in parsed_observations
            if item["parsed"] == candidate or item in conflicting
        ],
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
            "correct": sum(bool(item["correct"]) for item in samples),
            "false_accepts": sum(not bool(item["correct"]) for item in samples),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计 Server 完整日期与 Mobile 跨几何重复支持规则"
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
