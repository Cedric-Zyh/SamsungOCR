from __future__ import annotations

import argparse
import copy
import json
import sqlite3
from pathlib import Path

from receipt_ocr.analyzer import (
    _missing_month_day_component_prefilter_from_artifacts,
    _partial_year_missing_month_business_consensus_from_artifacts,
)
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.paddle_ocr import recognize_line


ARTIFACT_PREFIX = "/files/artifacts/"


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


def _artifact_path(storage: Path, url: str) -> Path | None:
    if not str(url).startswith(ARTIFACT_PREFIX):
        return None
    root = (storage / "artifacts").resolve()
    path = (root / str(url)[len(ARTIFACT_PREFIX):]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _mobile_month_context_variants(
    tight: dict, storage: Path,
) -> list[dict]:
    processed = _artifact_path(
        storage, str(tight.get("date_slot_month_context_processed_url") or "")
    )
    if processed is None:
        return []
    line_clean = processed.with_name(
        processed.name.replace(
            "-max-channel.png", "-max-channel-line-clean.png"
        )
    )
    variants = []
    for preprocessing, path in (
        ("最大通道去彩色", processed),
        ("最大通道去彩色并去横线", line_clean),
    ):
        try:
            rows = recognize_line(path, model_variant="mobile")
        except Exception:
            rows = []
        variants.append({
            "slot": "月份上下文槽位",
            "method": f"Mobile {preprocessing}整行识别",
            "model": "mobile",
            "preprocessing": preprocessing,
            "ocr_texts": [row.text for row in rows],
            "parsed_components": [],
            "image": str(path.resolve()),
        })
    return variants


def build_report(database: Path, truth_path: Path, storage: Path) -> dict:
    truth = load_ground_truth(truth_path)
    latest = _latest_hybrid_results(database)
    prefiltered = []
    accepted = []
    for filename, expected_row in truth.items():
        result = latest.get(filename)
        if result is None or (result.get("date_check") or {}).get(
            "reliable"
        ) is True:
            continue
        artifacts = copy.deepcopy(
            (result.get("processing_artifacts") or {}).get("date", [])
        )
        prefilter = _missing_month_day_component_prefilter_from_artifacts(
            artifacts
        )
        if prefilter is None:
            continue
        tight = next(
            (item for item in artifacts if item.get("variant") == "紧凑区域"),
            None,
        )
        if tight is None:
            continue
        probe_variants = _mobile_month_context_variants(tight, storage)
        tight.setdefault("date_slot_ocr_variants", []).extend(probe_variants)
        fields = result.get("fields") or {}
        selected = _partial_year_missing_month_business_consensus_from_artifacts(
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
            "day_support": prefilter["support"],
            "month_probe": probe_variants,
            "month_support": selected["month_support"] if selected else {},
        }
        prefiltered.append(row)
        if candidate is not None:
            accepted.append(row)
    false_accepts = [
        row["filename"] for row in accepted if not row["correct"]
    ]
    return {
        "backend": "hybrid",
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "prefiltered": len(prefiltered),
        "accepted": len(accepted),
        "correct_accepts": sum(row["correct"] for row in accepted),
        "false_accepts": false_accepts,
        "safe": not false_accepts,
        "samples": prefiltered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计残缺年份/缺月份日期的跨模型组件共识"
    )
    parser.add_argument(
        "--database", type=Path, default=Path("storage/results.db")
    )
    parser.add_argument(
        "--truth", type=Path, default=Path("数据/ground_truth.json")
    )
    parser.add_argument("--storage", type=Path, default=Path("storage"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database, args.truth, args.storage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: report[key]
        for key in (
            "truth_samples", "latest_results", "prefiltered", "accepted",
            "correct_accepts", "false_accepts", "safe",
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
