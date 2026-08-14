from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_ocr.database import Database
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.seal_reference import (
    CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES,
    CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES,
    CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CHROMATIC_CONSENSUS_MIN_INLIER_RATIO,
    CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO,
    COLOR_MASK_MIN_CORRELATION,
    COLOR_MASK_MIN_DICE,
    COLOR_MASK_MIN_SCORE,
    COLOR_SIFT_MIN_CORRELATION,
    COLOR_SIFT_MIN_DICE,
    COLOR_SIFT_MIN_GOOD_MATCHES,
    COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
    COLOR_SIFT_MIN_INLIER_RATIO,
    COLOR_SIFT_MIN_SCORE,
    COLOR_SIFT_MIN_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES,
    COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO,
    COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT,
    COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE,
    CONSENSUS_MIN_DISTINCT_REFERENCES,
    CONSENSUS_MIN_GOOD_MATCHES,
    CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CONSENSUS_MIN_INLIER_RATIO,
    CONSENSUS_MIN_SURFACE_COVERAGE,
    CONSENSUS_MIN_TOP_INLIERS,
    HIGH_RATIO_MIN_GOOD_MATCHES,
    HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
    HIGH_RATIO_MIN_INLIER_RATIO,
    HIGH_RATIO_MIN_SURFACE_COVERAGE,
    HIGH_SUPPORT_MIN_GOOD_MATCHES,
    HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    HIGH_SUPPORT_MIN_INLIER_RATIO,
    HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
    HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES,
    HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES,
    HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO,
    HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE,
    HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
    MIN_GOOD_MATCHES,
    MIN_HOMOGRAPHY_INLIERS,
    MIN_INLIER_RATIO,
    MIN_SURFACE_COVERAGE,
    SealReferenceMatcher,
    ULTRA_SUPPORT_MIN_GOOD_MATCHES,
    ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    ULTRA_SUPPORT_MIN_INLIER_RATIO,
    ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
)


def build_report(
    database: Database,
    truth_path: str | Path,
    backend: str = "hybrid",
) -> dict:
    truth = load_ground_truth(truth_path)
    matcher = SealReferenceMatcher(database.path.parent / "artifacts")
    reference_images = matcher.refresh(database, truth)
    results = database.list_results(
        limit=5000,
        filters={"ocr_backend": backend},
        latest_by_filename=True,
    )
    samples = []
    stored_accepted = 0
    stored_false_accepts = 0
    remaining_accepted = 0
    remaining_false_accepts = 0
    for result in results:
        filename = str(result.get("filename") or "")
        expected = truth.get(filename)
        if expected is None:
            continue
        seal_check = result.get("seal_check") or {}
        evidence = seal_check.get("visual_reference_match")
        source = "已保存生产证据" if evidence else "当前剩余样本矩阵"
        # Re-evaluate every still-unreliable row with the current algorithm.
        # A saved rejected attempt belongs to an older boundary and must not
        # hide a newly added, still-conservative route in this audit.
        if seal_check.get("reliable") is not True:
            evidence = matcher.match(result)
            source = "当前剩余样本矩阵"
        if not evidence or "reference_filename" not in evidence:
            if expected.get("seal_should_match") is False:
                samples.append({
                    "filename": filename,
                    "truth_should_match": False,
                    "company_conflict": bool(
                        seal_check.get("company_conflict")
                    ),
                    "accepted": False,
                    "reason": str((evidence or {}).get("reason") or ""),
                    "source": source,
                })
            continue
        accepted = bool(evidence.get("accepted"))
        truth_should_match = bool(expected.get("seal_should_match"))
        if source == "已保存生产证据" and accepted:
            stored_accepted += 1
            stored_false_accepts += int(not truth_should_match)
        elif accepted:
            remaining_accepted += 1
            remaining_false_accepts += int(not truth_should_match)
        samples.append({
            "filename": filename,
            "truth_should_match": truth_should_match,
            "requirement": str(seal_check.get("requirement") or ""),
            "company_conflict": bool(seal_check.get("company_conflict")),
            "accepted": accepted,
            "route": str(evidence.get("route") or ""),
            "source": source,
            "reference_filename": evidence.get("reference_filename", ""),
            "good_matches": int(evidence.get("good_matches", 0)),
            "homography_inliers": int(
                evidence.get("homography_inliers", 0)
            ),
            "inlier_ratio": float(evidence.get("inlier_ratio", 0)),
            "candidate_coverage": float(
                evidence.get("candidate_coverage", 0)
            ),
            "reference_coverage": float(
                evidence.get("reference_coverage", 0)
            ),
            "chromatic_good_matches": int(
                evidence.get("chromatic_good_matches", 0)
            ),
            "chromatic_homography_inliers": int(
                evidence.get("chromatic_homography_inliers", 0)
            ),
            "chromatic_inlier_ratio": float(
                evidence.get("chromatic_inlier_ratio", 0)
            ),
            "chromatic_candidate_coverage": float(
                evidence.get("chromatic_candidate_coverage", 0)
            ),
            "chromatic_reference_coverage": float(
                evidence.get("chromatic_reference_coverage", 0)
            ),
            "color_mask_score": float(
                evidence.get("color_mask_score", 0)
            ),
            "color_mask_correlation": float(
                evidence.get("color_mask_correlation", 0)
            ),
            "color_mask_dice": float(
                evidence.get("color_mask_dice", 0)
            ),
            "reason": str(evidence.get("reason") or ""),
            "consensus_reference_count": int(
                evidence.get("consensus_reference_count", 0)
            ),
            "consensus_matches": evidence.get("consensus_matches", []),
        })
    return {
        "backend": backend,
        "truth_samples": len(truth),
        "current_results": len([
            item for item in results if item.get("filename") in truth
        ]),
        "reference_images": reference_images,
        "thresholds": {
            "strict": {
                "good_matches": MIN_GOOD_MATCHES,
                "homography_inliers": MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": MIN_INLIER_RATIO,
                "surface_coverage": MIN_SURFACE_COVERAGE,
            },
            "high_ratio": {
                "good_matches": HIGH_RATIO_MIN_GOOD_MATCHES,
                "homography_inliers": HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": HIGH_RATIO_MIN_INLIER_RATIO,
                "surface_coverage": HIGH_RATIO_MIN_SURFACE_COVERAGE,
            },
            "high_support": {
                "good_matches": HIGH_SUPPORT_MIN_GOOD_MATCHES,
                "homography_inliers": HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": HIGH_SUPPORT_MIN_INLIER_RATIO,
                "surface_coverage": HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
            },
            "ultra_support": {
                "good_matches": ULTRA_SUPPORT_MIN_GOOD_MATCHES,
                "homography_inliers": ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": ULTRA_SUPPORT_MIN_INLIER_RATIO,
                "surface_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
            },
            "company_conflict_ultra_reference": {
                "good_matches": (
                    COMPANY_CONFLICT_REFERENCE_MIN_GOOD_MATCHES
                ),
                "homography_inliers": (
                    COMPANY_CONFLICT_REFERENCE_MIN_HOMOGRAPHY_INLIERS
                ),
                "inlier_ratio": (
                    COMPANY_CONFLICT_REFERENCE_MIN_INLIER_RATIO
                ),
                "surface_coverage": (
                    COMPANY_CONFLICT_REFERENCE_MIN_SURFACE_COVERAGE
                ),
                "chromatic_good_matches": (
                    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES
                ),
                "chromatic_homography_inliers": (
                    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS
                ),
                "chromatic_inlier_ratio": (
                    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO
                ),
                "chromatic_surface_coverage": (
                    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE
                ),
                "company_score": (
                    COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE
                ),
                "shared_company_fragment": (
                    COMPANY_CONFLICT_REFERENCE_MIN_SHARED_FRAGMENT
                ),
                "specific_stamp_type": "售后专用章",
            },
            "consensus": {
                "distinct_references": CONSENSUS_MIN_DISTINCT_REFERENCES,
                "top_inliers": CONSENSUS_MIN_TOP_INLIERS,
                "good_matches": CONSENSUS_MIN_GOOD_MATCHES,
                "homography_inliers": CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": CONSENSUS_MIN_INLIER_RATIO,
                "surface_coverage": CONSENSUS_MIN_SURFACE_COVERAGE,
            },
            "high_purity_consensus": {
                "distinct_references": (
                    HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES
                ),
                "top_inliers": HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
                "good_matches": HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES,
                "homography_inliers": (
                    HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
                ),
                "inlier_ratio": HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO,
                "surface_coverage": (
                    HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
                ),
            },
            "chromatic_crop_consensus": {
                "distinct_references": (
                    CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES
                ),
                "good_matches": CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES,
                "homography_inliers": (
                    CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
                ),
                "inlier_ratio": CHROMATIC_CONSENSUS_MIN_INLIER_RATIO,
                "surface_coverage": (
                    CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
                ),
                "top_inliers": CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
                "top_inlier_ratio": (
                    CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO
                ),
            },
            "color_mask": {
                "score": COLOR_MASK_MIN_SCORE,
                "correlation": COLOR_MASK_MIN_CORRELATION,
                "dice": COLOR_MASK_MIN_DICE,
            },
            "color_sift": {
                "score": COLOR_SIFT_MIN_SCORE,
                "correlation": COLOR_SIFT_MIN_CORRELATION,
                "dice": COLOR_SIFT_MIN_DICE,
                "good_matches": COLOR_SIFT_MIN_GOOD_MATCHES,
                "homography_inliers": COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": COLOR_SIFT_MIN_INLIER_RATIO,
                "surface_coverage": COLOR_SIFT_MIN_SURFACE_COVERAGE,
            },
        },
        "summary": {
            "stored_accepted": stored_accepted,
            "stored_false_accepts": stored_false_accepts,
            "remaining_accepted": remaining_accepted,
            "remaining_false_accepts": remaining_false_accepts,
            "known_negative_controls": sum(
                1 for entry in truth.values()
                if entry.get("seal_should_match") is False
            ),
        },
        "samples": sorted(
            samples,
            key=lambda item: (
                not bool(item.get("accepted")),
                -int(item.get("homography_inliers", 0)),
                str(item.get("filename", "")),
            ),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计人工真值参考章的 SIFT/RANSAC 安全矩阵"
    )
    parser.add_argument("--database", type=Path, default=Path("storage/results.db"))
    parser.add_argument("--truth", type=Path, default=Path("数据/ground_truth.json"))
    parser.add_argument("--backend", default="hybrid")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(Database(args.database), args.truth, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
