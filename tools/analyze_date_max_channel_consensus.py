from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.analyzer import (
    _cross_model_max_channel_required_with_truncated_conflict,
)
from receipt_ocr.ocr_types import TextObservation
from receipt_ocr.parser import parse_date, parse_receipt_date
from tools.analyze_date_gaps import _latest_original_results, _observations


MAX_CHANNEL_MARKER = "日期行最大通道去彩色三倍放大"


def select_saved_candidate(result: dict) -> dict:
    """Audit the strict three-cell consensus route without ground truth."""
    date_check = result.get("date_check") or {}
    required = parse_date(str(date_check.get("required") or ""))
    if date_check.get("reliable") is True or required is None:
        return {"accepted": False, "reason": "当前日期已可靠或要求日期无效"}

    by_cell: dict[tuple[str, bool], dict] = {}
    display_evidence: list[dict] = []
    for artifact in (result.get("processing_artifacts") or {}).get("date") or []:
        variant = str(artifact.get("variant") or "")
        if variant == "紧凑区域":
            tight = True
        elif variant == "宽区域":
            tight = False
        else:
            continue
        for evidence in artifact.get("date_line_ocr_variants") or []:
            preprocessing = str(evidence.get("preprocessing") or "")
            if MAX_CHANNEL_MARKER not in preprocessing:
                continue
            model = (
                "mobile" if "Mobile" in preprocessing
                else "server" if "Server" in preprocessing
                else ""
            )
            if not model:
                continue
            strict_texts = [
                str(text) for text in evidence.get("ocr_texts") or []
                if parse_date(str(text)) == required
            ]
            if not strict_texts:
                continue
            rows = [
                TextObservation(text, 1.0, 0, 0, 1, 1)
                for text in strict_texts
            ]
            by_cell[(model, tight)] = {
                "model": model,
                "tight": tight,
                "compact": False,
                "rows": rows,
            }
            display_evidence.append({
                "model": model,
                "geometry": "tight" if tight else "wide",
                "texts": strict_texts,
                "preprocessing": preprocessing,
            })

    conflicts = {
        parsed
        for observation in _observations(result)
        if (parsed := parse_receipt_date(
            str(observation.get("text") or ""), required
        )) is not None
        and parsed != required
    }
    accepted = _cross_model_max_channel_required_with_truncated_conflict(
        list(by_cell.values()), conflicts, required
    )
    if accepted is None:
        return {
            "accepted": False,
            "reason": "未形成三单元格完整日期共识或冲突非单字截断",
            "cells": sorted(
                f"{model}/{'tight' if tight else 'wide'}"
                for model, tight in by_cell
            ),
            "conflicts": sorted(value.isoformat() for value in conflicts),
        }
    return {
        "accepted": True,
        "candidate": required.isoformat(),
        "reason": "三个模型×几何单元格完整一致，唯一冲突为两位日单字截断",
        "cells": sorted(
            f"{model}/{'tight' if tight else 'wide'}"
            for model, tight in by_cell
        ),
        "conflicts": sorted(value.isoformat() for value in conflicts),
        "evidence": display_evidence,
    }


def build_report(database: Path, truth_path: Path, backend: str) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    samples = []
    false_accepts = 0
    for filename, expected in truth.items():
        result = latest.get(filename)
        if result is None:
            continue
        audit = select_saved_candidate(result)
        if not audit.get("accepted"):
            continue
        truth_date = str(expected.get("actual_date") or "")
        correct = audit.get("candidate") == truth_date
        false_accepts += int(not correct)
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": correct,
            **audit,
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "accepted": len(samples),
            "correct": sum(bool(item["correct"]) for item in samples),
            "false_accepts": false_accepts,
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计最大通道日期三单元格共识与单字截断冲突"
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
