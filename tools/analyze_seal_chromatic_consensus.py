from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from receipt_ocr.database import Database
from receipt_ocr.evaluation import load_ground_truth
from receipt_ocr.parser import normalize_text
from receipt_ocr.seal_reference import SealReferenceMatcher


MIN_DISTINCT_REFERENCES = 2
MIN_GOOD_MATCHES = 100
MIN_HOMOGRAPHY_INLIERS = 65
MIN_INLIER_RATIO = 0.68
MIN_SURFACE_COVERAGE = 0.24
MIN_TOP_INLIERS = 95
MIN_TOP_INLIER_RATIO = 0.85


class ChromaticCropComparator:
    """SIFT comparator whose crop bounds ignore black form/pen residue."""

    def __init__(self) -> None:
        self.sift = cv2.SIFT_create(
            nfeatures=1200, contrastThreshold=0.02, edgeThreshold=12
        )
        self.matcher = cv2.BFMatcher()
        self.cache: dict[
            str, tuple[list, np.ndarray | None, tuple[int, int]]
        ] = {}

    def compare(self, candidate: Path, reference: Path) -> dict:
        left = self._descriptors(candidate)
        right = self._descriptors(reference)
        left_points, left_descriptors, left_shape = left
        right_points, right_descriptors, right_shape = right
        if (
            left_descriptors is None
            or right_descriptors is None
            or len(left_points) < 8
            or len(right_points) < 8
        ):
            return _empty_metrics()
        pairs = self.matcher.knnMatch(
            left_descriptors, right_descriptors, k=2
        )
        good = [
            first for first, second in pairs
            if first.distance < 0.72 * second.distance
        ]
        if len(good) < 6:
            return {**_empty_metrics(), "good_matches": len(good)}
        candidate_points = np.float32([
            left_points[item.queryIdx].pt for item in good
        ])
        reference_points = np.float32([
            right_points[item.trainIdx].pt for item in good
        ])
        _homography, mask = cv2.findHomography(
            candidate_points.reshape(-1, 1, 2),
            reference_points.reshape(-1, 1, 2),
            cv2.RANSAC,
            5.0,
        )
        if mask is None:
            return {**_empty_metrics(), "good_matches": len(good)}
        inlier_mask = mask.ravel().astype(bool)
        inliers = int(inlier_mask.sum())
        return {
            "good_matches": len(good),
            "homography_inliers": inliers,
            "inlier_ratio": round(inliers / max(1, len(good)), 4),
            "candidate_coverage": round(
                _point_coverage(
                    candidate_points[inlier_mask], left_shape
                ),
                4,
            ),
            "reference_coverage": round(
                _point_coverage(
                    reference_points[inlier_mask], right_shape
                ),
                4,
            ),
        }

    def _descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        key = str(path)
        if key in self.cache:
            return self.cache[key]
        image = cv2.imdecode(
            np.fromfile(key, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            value = ([], None, (1, 1))
            self.cache[key] = value
            return value
        blue, green, red = cv2.split(image)
        high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
        low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
        chromatic = ((high - low) >= 22) & (low <= 235)
        ys, xs = np.where(chromatic)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(xs) >= 180:
            pad = 8
            x1 = max(0, int(xs.min()) - pad)
            x2 = min(gray.shape[1], int(xs.max()) + pad + 1)
            y1 = max(0, int(ys.min()) - pad)
            y2 = min(gray.shape[0], int(ys.max()) + pad + 1)
            gray = gray[y1:y2, x1:x2]
        scale = 800 / max(1, max(gray.shape))
        if scale < 1.0 or scale > 1.2:
            gray = cv2.resize(
                gray,
                None,
                fx=scale,
                fy=scale,
                interpolation=(
                    cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                ),
            )
        keypoints, descriptors = self.sift.detectAndCompute(gray, None)
        value = (keypoints or [], descriptors, gray.shape[:2])
        self.cache[key] = value
        return value


def _empty_metrics() -> dict:
    return {
        "good_matches": 0,
        "homography_inliers": 0,
        "inlier_ratio": 0.0,
        "candidate_coverage": 0.0,
        "reference_coverage": 0.0,
    }


def _point_coverage(points: np.ndarray, shape: tuple[int, int]) -> float:
    if len(points) < 2:
        return 0.0
    _x, _y, width, height = cv2.boundingRect(np.float32(points))
    return (width * height) / max(1, shape[0] * shape[1])


def _vote_passes(metrics: dict) -> bool:
    return bool(
        metrics["good_matches"] >= MIN_GOOD_MATCHES
        and metrics["homography_inliers"] >= MIN_HOMOGRAPHY_INLIERS
        and metrics["inlier_ratio"] >= MIN_INLIER_RATIO
        and metrics["candidate_coverage"] >= MIN_SURFACE_COVERAGE
        and metrics["reference_coverage"] >= MIN_SURFACE_COVERAGE
    )


def _evaluate_group(rows: list[dict]) -> dict:
    by_candidate: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        if not _vote_passes(row):
            continue
        candidate_index = int(row["candidate_index"])
        filename = str(row["reference_filename"])
        previous = by_candidate[candidate_index].get(filename)
        if previous is None or _rank(row) > _rank(previous):
            by_candidate[candidate_index][filename] = row
    groups = []
    for candidate_index, references in by_candidate.items():
        matches = sorted(references.values(), key=_rank, reverse=True)
        top = matches[0]
        groups.append({
            "candidate_index": candidate_index,
            "reference_count": len(matches),
            "accepted": bool(
                len(matches) >= MIN_DISTINCT_REFERENCES
                and top["homography_inliers"] >= MIN_TOP_INLIERS
                and top["inlier_ratio"] >= MIN_TOP_INLIER_RATIO
            ),
            "matches": matches,
        })
    if not groups:
        return {
            "accepted": False,
            "candidate_index": -1,
            "reference_count": 0,
            "matches": [],
        }
    return max(
        groups,
        key=lambda item: (
            bool(item["accepted"]),
            int(item["reference_count"]),
            _rank(item["matches"][0]),
        ),
    )


def _rank(row: dict) -> tuple:
    return (
        int(row["homography_inliers"]),
        float(row["inlier_ratio"]),
        min(
            float(row["candidate_coverage"]),
            float(row["reference_coverage"]),
        ),
        int(row["good_matches"]),
    )


def build_report(database: Database, truth_path: Path) -> dict:
    truth = load_ground_truth(truth_path)
    reference_matcher = SealReferenceMatcher(
        database.path.parent / "artifacts"
    )
    reference_matcher.refresh(database, truth)
    comparator = ChromaticCropComparator()
    results = database.list_results(
        limit=5000,
        filters={"ocr_backend": "hybrid"},
        latest_by_filename=True,
    )
    samples = []
    accepted = false_accepts = 0
    for result in results:
        filename = str(result.get("filename") or "")
        expected = truth.get(filename)
        seal_check = result.get("seal_check") or {}
        if expected is None:
            continue
        rejection_reason = ""
        if seal_check.get("reliable") is True:
            rejection_reason = "已有可靠结论，不重复促进"
        elif seal_check.get("company_conflict") is True:
            rejection_reason = "完整公司名冲突硬阻断"
        elif not str(seal_check.get("recognized") or "").strip():
            rejection_reason = "没有可用印章 OCR 文字"
        if rejection_reason:
            if expected.get("seal_should_match") is False:
                samples.append({
                    "filename": filename,
                    "truth_should_match": False,
                    "requirement": str(
                        seal_check.get("requirement") or ""
                    ),
                    "accepted": False,
                    "candidate_index": -1,
                    "reference_count": 0,
                    "matches": [],
                    "reason": rejection_reason,
                })
            continue
        requirement = normalize_text(
            str(seal_check.get("requirement") or "")
        )
        references = [
            item for item in reference_matcher.references.get(
                requirement, []
            )
            if item.filename != filename
        ]
        rows = []
        for artifact in result.get(
            "processing_artifacts", {}
        ).get("seals", []):
            candidate_path = reference_matcher._artifact_path(
                str(artifact.get("color_isolated_url") or "")
            )
            if candidate_path is None:
                continue
            for reference in references:
                rows.append({
                    **comparator.compare(candidate_path, reference.path),
                    "candidate_index": int(artifact.get("index", -1)),
                    "candidate_url": str(
                        artifact.get("color_isolated_url") or ""
                    ),
                    "reference_filename": reference.filename,
                    "reference_url": reference.artifact_url,
                })
        group = _evaluate_group(rows)
        if group["accepted"]:
            accepted += 1
            false_accepts += int(
                expected.get("seal_should_match") is not True
            )
        if group["accepted"] or expected.get("seal_should_match") is False:
            samples.append({
                "filename": filename,
                "truth_should_match": expected.get("seal_should_match"),
                "requirement": str(seal_check.get("requirement") or ""),
                **group,
            })
    return {
        "truth_samples": len(truth),
        "thresholds": {
            "distinct_references": MIN_DISTINCT_REFERENCES,
            "good_matches": MIN_GOOD_MATCHES,
            "homography_inliers": MIN_HOMOGRAPHY_INLIERS,
            "inlier_ratio": MIN_INLIER_RATIO,
            "surface_coverage": MIN_SURFACE_COVERAGE,
            "top_inliers": MIN_TOP_INLIERS,
            "top_inlier_ratio": MIN_TOP_INLIER_RATIO,
        },
        "summary": {
            "accepted": accepted,
            "false_accepts": false_accepts,
            "known_negative_controls": sum(
                1 for entry in truth.values()
                if entry.get("seal_should_match") is False
            ),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="审计排除黑色表格/签字噪声后的印章多参考 SIFT 共识"
    )
    parser.add_argument(
        "--database", type=Path, default=Path("storage/results.db")
    )
    parser.add_argument(
        "--truth", type=Path, default=Path("数据/ground_truth.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(Database(args.database), args.truth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
