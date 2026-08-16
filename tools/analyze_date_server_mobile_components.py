from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from receipt_ocr.analyzer import (
    _server_cross_geometry_date_with_mobile_components_from_artifacts,
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


def _evidence(artifacts: list[dict]) -> list[dict]:
    output = []
    for artifact in artifacts:
        variant = str(artifact.get("variant", ""))
        if variant not in {"紧凑区域", "宽区域"}:
            continue
        for group in (
            "ocr_variants", "secondary_ocr_variants",
            "date_line_ocr_variants",
        ):
            for item in artifact.get(group) or []:
                preprocessing = str(item.get("preprocessing", ""))
                texts = [str(text) for text in item.get("ocr_texts") or []]
                if texts:
                    output.append({
                        "variant": variant,
                        "group": group,
                        "preprocessing": preprocessing,
                        "ocr_texts": texts,
                    })
    return output


def build_report(database: Path, truth_path: Path) -> dict:
    truth = load_ground_truth(truth_path)
    latest = _latest_hybrid_results(database)
    audited = []
    accepted = []
    for filename, expected_row in truth.items():
        result = latest.get(filename)
        if result is None:
            continue
        date_check = result.get("date_check") or {}
        if date_check.get("reliable") is True:
            continue
        artifacts = (result.get("processing_artifacts") or {}).get(
            "date", []
        )
        if not artifacts:
            continue
        candidate = (
            _server_cross_geometry_date_with_mobile_components_from_artifacts(
                artifacts
            )
        )
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
            "evidence": _evidence(artifacts) if candidate else [],
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
        description="审计 Server 双几何完整日期与 Mobile 残缺组件共识"
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
