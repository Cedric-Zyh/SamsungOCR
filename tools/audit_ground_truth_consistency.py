from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.evaluation import GROUND_TRUTH_FIELDS
from receipt_ocr.parser import normalize_text
from tools.analyze_date_gaps import _latest_original_results


def field_mismatches(
    result: dict, expected_fields: dict, *, threshold: float = 0.95,
) -> list[dict]:
    """Return review-only suspects where OCR and truth disagree.

    A mismatch is highlighted only when the stored field has high confidence
    and the exact recognized value also occurs in the independently stored raw
    page transcript. This never edits truth or declares OCR correct; it merely
    makes a likely labeling drift visible for a human to compare with the
    original sample.
    """
    fields = result.get("fields") or {}
    metadata = result.get("field_metadata") or {}
    raw_text = normalize_text(str(result.get("raw_text") or ""))
    seal_requirement = normalize_text(str(
        (result.get("seal_check") or {}).get("requirement") or ""
    ))
    suspects = []
    for name in GROUND_TRUTH_FIELDS:
        if name not in expected_fields:
            continue
        expected = str(expected_fields.get(name) or "")
        actual = str(fields.get(name) or "")
        normalized_expected = normalize_text(expected)
        normalized_actual = normalize_text(actual)
        if normalized_actual == normalized_expected:
            continue
        field_meta = metadata.get(name) or {}
        confidence = float(field_meta.get("confidence") or 0)
        raw_text_support = bool(
            normalized_actual and normalized_actual in raw_text
        )
        seal_pipeline_support = bool(
            name == "签章要求"
            and normalized_actual
            and seal_requirement == normalized_actual
        )
        suspects.append({
            "field": name,
            "expected": expected,
            "recognized": actual,
            "confidence": round(confidence, 4),
            "source": str(field_meta.get("source") or ""),
            "raw_text_support": raw_text_support,
            "seal_pipeline_support": seal_pipeline_support,
            "needs_truth_review": bool(
                confidence >= threshold and raw_text_support
            ),
        })
    return suspects


def build_report(
    database: Path, truth_path: Path, backend: str, threshold: float,
) -> dict:
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    latest = _latest_original_results(database, backend)
    samples = []
    comparison_count = mismatch_count = review_count = 0
    for filename, expected in truth.items():
        result = latest.get(filename)
        if result is None:
            continue
        expected_fields = expected.get("fields") or {}
        comparison_count += sum(
            name in expected_fields for name in GROUND_TRUTH_FIELDS
        )
        mismatches = field_mismatches(
            result, expected_fields, threshold=threshold
        )
        mismatch_count += len(mismatches)
        review_items = [
            item for item in mismatches if item["needs_truth_review"]
        ]
        review_count += len(review_items)
        if mismatches:
            samples.append({
                "filename": filename,
                "result_id": result.get("id"),
                "mismatches": mismatches,
            })
    return {
        "backend": backend,
        "threshold": threshold,
        "truth_samples": len(truth),
        "latest_results": len(latest),
        "summary": {
            "field_comparisons": comparison_count,
            "field_mismatches": mismatch_count,
            "high_confidence_raw_supported_truth_review": review_count,
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计高置信 OCR 与人工字段真值的不一致项（只报警，不自动修改）"
    )
    parser.add_argument(
        "--database", type=Path, default=Path("storage/results.db")
    )
    parser.add_argument(
        "--truth", type=Path, default=Path("数据/ground_truth.json")
    )
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        args.database, args.truth, args.backend, args.threshold
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
