from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from receipt_ocr.analyzer import (
    _cross_model_far_lower_complementary_date,
    _repeated_strict_date_across_variants,
    _save_right_padded_date_line,
)
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.ocr_backends import recognize_text


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
    prefix = "/files/artifacts/"
    if not str(url).startswith(prefix):
        return None
    path = (storage / "artifacts" / str(url)[len(prefix):]).resolve()
    artifact_root = (storage / "artifacts").resolve()
    try:
        path.relative_to(artifact_root)
    except ValueError:
        return None
    return path if path.is_file() else None


def build_report(
    database: Path,
    truth_path: Path,
    storage: Path,
    padded_dir: Path,
) -> dict:
    truth = load_ground_truth(truth_path)
    latest = _latest_hybrid_results(database)
    samples = []
    for filename, result in latest.items():
        if (result.get("date_check") or {}).get("reliable") is True:
            continue
        far = next((
            item
            for item in (result.get("processing_artifacts") or {}).get(
                "date", []
            )
            if item.get("variant") == "远下方手写日期复核区域"
        ), None)
        if far is None:
            continue
        mobile_variants = far.get("secondary_ocr_variants") or []
        mobile_candidate = _repeated_strict_date_across_variants(
            mobile_variants
        )
        if mobile_candidate is None:
            continue
        source = _artifact_path(
            storage, str(far.get("date_line_original_url") or "")
        )
        if source is None:
            continue
        padded_dir.mkdir(parents=True, exist_ok=True)
        padded = padded_dir / f"{Path(filename).stem}-right-padded.png"
        _save_right_padded_date_line(source, padded)
        padded_mobile = recognize_text(
            padded, backend="paddle", min_text_height=0.012
        )
        padded_server = recognize_text(
            padded, backend="paddle_server", min_text_height=0.012
        )
        existing_texts = [
            str(text)
            for artifact in (result.get("processing_artifacts") or {}).get(
                "date", []
            )
            for group in (
                "ocr_variants", "secondary_ocr_variants",
                "date_line_ocr_variants",
            )
            for variant in artifact.get(group) or []
            for text in variant.get("ocr_texts") or []
        ]
        accepted = _cross_model_far_lower_complementary_date(
            mobile_variants,
            far.get("far_lower_server_variants") or [],
            [row.text for row in padded_mobile],
            [row.text for row in padded_server],
            existing_texts,
        )
        expected = str((truth.get(filename) or {}).get("actual_date") or "")
        samples.append({
            "filename": filename,
            "truth": expected,
            "mobile_region_candidate": mobile_candidate.isoformat(),
            "server_line_variants": far.get(
                "far_lower_server_variants"
            ) or [],
            "padded_mobile": [row.text for row in padded_mobile],
            "padded_server": [row.text for row in padded_server],
            "padded_artifact": str(padded.resolve()),
            "accepted": accepted is not None,
            "candidate": accepted.isoformat() if accepted else "",
            "correct": bool(accepted and accepted.isoformat() == expected),
        })
    accepted_rows = [row for row in samples if row["accepted"]]
    false_accepts = [row["filename"] for row in accepted_rows if not row["correct"]]
    return {
        "backend": "hybrid",
        "truth_samples": len(truth),
        "prefilter_candidates": len(samples),
        "accepted": len(accepted_rows),
        "correct_accepts": sum(row["correct"] for row in accepted_rows),
        "false_accepts": false_accepts,
        "safe": not false_accepts,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计远下方日期行右侧补白的跨模型互补证据"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--storage", type=Path, default=Path("storage"))
    parser.add_argument(
        "--padded-dir", type=Path,
        default=Path("tmp/date-far-lower-padding-audit"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        args.database, args.truth, args.storage, args.padded_dir
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        key: report[key]
        for key in (
            "truth_samples", "prefilter_candidates", "accepted",
            "correct_accepts", "false_accepts", "safe",
        )
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
