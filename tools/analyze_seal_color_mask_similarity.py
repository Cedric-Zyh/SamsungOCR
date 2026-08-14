from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_ocr.database import Database
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.parser import normalize_text
from receipt_ocr.seal_reference import (
    SealReferenceMatcher,
    _color_mask_similarity,
    _normalized_color_ink,
)


def color_mask_similarity(candidate: Path, reference: Path) -> dict:
    metrics = _color_mask_similarity(
        _normalized_color_ink(candidate), _normalized_color_ink(reference)
    )
    return {
        key.removeprefix("color_mask_"): value
        for key, value in metrics.items()
    }


def build_report(database: Database, truth_path: Path, backend: str) -> dict:
    truth = load_ground_truth(truth_path)
    matcher = SealReferenceMatcher(database.path.parent / "artifacts")
    reference_images = matcher.refresh(database, truth)
    results = database.list_results(
        limit=5000,
        filters={"ocr_backend": backend},
        latest_by_filename=True,
    )
    samples = []
    for result in results:
        filename = str(result.get("filename") or "")
        expected = truth.get(filename)
        seal_check = result.get("seal_check") or {}
        if expected is None or seal_check.get("reliable") is True:
            continue
        if seal_check.get("company_conflict") is True:
            continue
        requirement = normalize_text(str(seal_check.get("requirement") or ""))
        references = [
            reference for reference in matcher.references.get(requirement, [])
            if reference.filename != filename
        ]
        if not references:
            continue
        best = None
        for artifact in (result.get("processing_artifacts") or {}).get("seals") or []:
            candidate_url = str(artifact.get("color_isolated_url") or "")
            candidate_path = matcher._artifact_path(candidate_url)
            if candidate_path is None:
                continue
            for reference in references:
                metrics = color_mask_similarity(candidate_path, reference.path)
                row = {
                    **metrics,
                    "candidate_index": int(artifact.get("index", -1)),
                    "candidate_url": candidate_url,
                    "reference_filename": reference.filename,
                    "reference_url": reference.artifact_url,
                }
                if best is None or row["score"] > best["score"]:
                    best = row
        if best is None:
            continue
        samples.append({
            "filename": filename,
            "truth_should_match": bool(expected.get("seal_should_match")),
            "requirement": str(seal_check.get("requirement") or ""),
            "recognized": str(seal_check.get("recognized") or ""),
            "company_score": float(seal_check.get("company_score", 0)),
            **best,
        })
    positives = [row for row in samples if row["truth_should_match"]]
    negatives = [row for row in samples if not row["truth_should_match"]]
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "reference_images": reference_images,
        "summary": {
            "scored": len(samples),
            "positive_scored": len(positives),
            "negative_scored": len(negatives),
            "positive_max": max((row["score"] for row in positives), default=0),
            "negative_max": max((row["score"] for row in negatives), default=0),
        },
        "samples": sorted(samples, key=lambda row: row["score"], reverse=True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="审计彩色印章整体墨迹相似度")
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(Database(args.database), args.truth, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
