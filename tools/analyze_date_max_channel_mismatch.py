from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from receipt_ocr.parser import parse_date, parse_receipt_date
from tools.analyze_date_gaps import _latest_original_results, _observations


MAX_CHANNEL_MARKER = "日期行最大通道去彩色三倍放大"


def select_saved_candidate(result: dict) -> dict:
    """Audit a strict non-required date without consulting ground truth."""
    date_check = result.get("date_check") or {}
    required = parse_date(str(date_check.get("required") or ""))
    if date_check.get("reliable") is True or required is None:
        return {"accepted": False, "reason": "当前日期已可靠或要求日期无效"}

    by_date: dict[str, list[dict]] = defaultdict(list)
    for artifact in (result.get("processing_artifacts") or {}).get("date") or []:
        variant = str(artifact.get("variant") or "")
        if variant == "紧凑区域":
            geometry = "tight"
        elif variant == "宽区域":
            geometry = "wide"
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
            for text in evidence.get("ocr_texts") or []:
                parsed = parse_date(str(text))
                if parsed is None or parsed == required:
                    continue
                by_date[parsed.isoformat()].append({
                    "model": model,
                    "geometry": geometry,
                    "text": str(text),
                    "variant": variant,
                    "preprocessing": preprocessing,
                })

    cross_geometry: dict[str, list[dict]] = {}
    for candidate, evidence in by_date.items():
        if any(
            left["model"] == "mobile"
            and right["model"] == "server"
            and left["geometry"] != right["geometry"]
            for left in evidence for right in evidence
        ):
            cross_geometry[candidate] = evidence
    if len(cross_geometry) != 1:
        return {
            "accepted": False,
            "reason": "未形成唯一的 Mobile/Server 跨几何完整不匹配日期",
            "cross_geometry_candidates": sorted(cross_geometry),
        }
    candidate = next(iter(cross_geometry))

    all_dates = {
        parsed.isoformat()
        for observation in _observations(result)
        if (parsed := parse_receipt_date(
            str(observation.get("text") or ""), required
        )) is not None
    }
    conflicts = sorted(all_dates - {candidate})
    if conflicts:
        return {
            "accepted": False,
            "candidate": candidate,
            "reason": "日期中间图仍存在其他可解析候选",
            "conflicts": conflicts,
            "evidence": cross_geometry[candidate],
        }
    creation = parse_date(str((result.get("fields") or {}).get("制单日期") or ""))
    parsed_candidate = parse_date(candidate)
    if creation is not None and parsed_candidate is not None and parsed_candidate < creation:
        return {
            "accepted": False,
            "candidate": candidate,
            "reason": "候选早于制单日期",
            "evidence": cross_geometry[candidate],
        }
    return {
        "accepted": True,
        "candidate": candidate,
        "required": required.isoformat(),
        "reason": "最大通道视图中 Mobile/Server 在紧宽不同裁剪读到唯一完整日期",
        "evidence": cross_geometry[candidate],
    }


def build_report(database: Path, truth_path: Path, backend: str) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    samples = []
    stored_accepted = stored_false_accepts = 0
    remaining_accepted = remaining_false_accepts = 0
    for filename, expected in truth.items():
        result = latest.get(filename)
        if result is None:
            continue
        stored_candidates = {
            str(artifact.get("date_max_channel_mismatch_candidate") or "")
            for artifact in (result.get("processing_artifacts") or {}).get(
                "date", []
            )
            if artifact.get("date_max_channel_mismatch_candidate")
        }
        if len(stored_candidates) == 1:
            audit = {
                "accepted": True,
                "candidate": next(iter(stored_candidates)),
                "reason": "已保存生产证据",
            }
            source = "已保存生产证据"
        else:
            audit = select_saved_candidate(result)
            source = "当前剩余样本矩阵"
        if not audit.get("accepted"):
            continue
        truth_date = str(expected.get("actual_date") or "")
        is_correct = str(audit.get("candidate") or "") == truth_date
        if source == "已保存生产证据":
            stored_accepted += 1
            stored_false_accepts += int(not is_correct)
        else:
            remaining_accepted += 1
            remaining_false_accepts += int(not is_correct)
        samples.append({
            "filename": filename,
            "truth": truth_date,
            "correct": is_correct,
            "source": source,
            **audit,
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "stored_accepted": stored_accepted,
            "stored_false_accepts": stored_false_accepts,
            "remaining_accepted": remaining_accepted,
            "remaining_false_accepts": remaining_false_accepts,
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计最大通道 Mobile/Server 跨几何不匹配日期"
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
