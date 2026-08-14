from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .parser import normalize_text


# These boundaries were selected against the complete 301-image human-truth
# set.  The two known wrong-stamp controls have 56/24 geometric inliers; the
# first accepted true stamp has 81.  Keep a visible margin instead of tuning
# to the last positive sample.
MIN_GOOD_MATCHES = 110
MIN_HOMOGRAPHY_INLIERS = 80
MIN_INLIER_RATIO = 0.65
MIN_SURFACE_COVERAGE = 0.30

# A narrowly lower match-count route is allowed only when RANSAC purity is
# materially higher.  This covers small crop/preprocessing jitter without
# weakening the original route: at least 80 correspondences must still be
# geometric inliers and at least 70% of all candidate matches must agree.
HIGH_RATIO_MIN_GOOD_MATCHES = 105
HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS = 80
HIGH_RATIO_MIN_INLIER_RATIO = 0.70
HIGH_RATIO_MIN_SURFACE_COVERAGE = 0.30

# Very dense, high-purity geometry can tolerate a tiny crop-boundary wobble.
# This is not a general coverage relaxation: both match and inlier counts are
# substantially above the normal route and the 28% floor still excludes the
# next high-inlier sample (24.4% candidate coverage).
HIGH_SUPPORT_MIN_GOOD_MATCHES = 130
HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS = 100
HIGH_SUPPORT_MIN_INLIER_RATIO = 0.70
HIGH_SUPPORT_MIN_SURFACE_COVERAGE = 0.28

# A second route can recover a repeated stamp that narrowly misses the
# single-reference inlier boundary.  It deliberately needs two *different*
# human-confirmed files to agree with the same detected candidate region.
# The leading reference still needs a visible margin over the strongest
# known wrong-stamp control (56 inliers), and every vote must retain the same
# ratio/coverage guarantees as the strict route.
CONSENSUS_MIN_DISTINCT_REFERENCES = 2
CONSENSUS_MIN_TOP_INLIERS = 75
CONSENSUS_MIN_GOOD_MATCHES = 98
CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
CONSENSUS_MIN_INLIER_RATIO = 0.65
CONSENSUS_MIN_SURFACE_COVERAGE = 0.30


@dataclass(frozen=True)
class SealReference:
    filename: str
    requirement: str
    artifact_url: str
    path: Path


class SealReferenceMatcher:
    """Match a new color-isolated seal to human-confirmed local references.

    OCR remains the primary evidence.  This matcher is a conservative second
    route for repeated customer stamps: references must come from a positive
    ground-truth row, the normalized signature requirement must be identical,
    and local feature matches must form a broad homography over both stamp
    surfaces.  A short logo/star match or a conflicting OCR company name can
    never promote a result.
    """

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root).resolve()
        self.references: dict[str, list[SealReference]] = {}
        self.truth_filenames: set[str] = set()
        self._descriptor_cache: dict[str, tuple[list, np.ndarray | None, tuple[int, int]]] = {}
        self._lock = threading.RLock()
        self._sift = cv2.SIFT_create(
            nfeatures=1200,
            contrastThreshold=0.02,
            edgeThreshold=12,
        ) if hasattr(cv2, "SIFT_create") else None
        self._matcher = cv2.BFMatcher()

    @property
    def enabled(self) -> bool:
        return self._sift is not None

    def refresh(self, database: Any, ground_truth: dict) -> int:
        """Rebuild the path-only gallery; descriptors remain lazy and cached."""
        grouped: dict[str, list[SealReference]] = defaultdict(list)
        truth_filenames = {
            filename
            for filename, entry in ground_truth.items()
            if entry.get("seal_should_match") is True
        }
        if self.enabled and truth_filenames:
            current = database.list_results(
                limit=5000,
                filters={"ocr_backend": "hybrid"},
                latest_by_filename=True,
            )
            for item in current:
                filename = str(item.get("filename") or "")
                truth = ground_truth.get(filename) or {}
                if filename not in truth_filenames:
                    continue
                seal_check = item.get("seal_check") or {}
                if seal_check.get("reliable") is not True:
                    continue
                requirement = normalize_text(
                    str(seal_check.get("requirement") or "")
                )
                truth_requirement = normalize_text(
                    str((truth.get("fields") or {}).get("签章要求") or "")
                )
                if not requirement or requirement != truth_requirement:
                    continue
                for artifact in (
                    item.get("processing_artifacts", {}).get("seals", [])
                ):
                    url = str(artifact.get("color_isolated_url") or "")
                    path = self._artifact_path(url)
                    if path is None:
                        continue
                    grouped[requirement].append(SealReference(
                        filename=filename,
                        requirement=requirement,
                        artifact_url=url,
                        path=path,
                    ))
        with self._lock:
            self.references = dict(grouped)
            self.truth_filenames = truth_filenames
            existing = {
                str(reference.path)
                for values in self.references.values()
                for reference in values
            }
            self._descriptor_cache = {
                key: value
                for key, value in self._descriptor_cache.items()
                if key in existing and Path(key).is_file()
            }
        return sum(len(values) for values in grouped.values())

    def match(self, result: dict) -> dict:
        seal_check = result.get("seal_check") or {}
        if (
            not self.enabled
            or seal_check.get("reliable") is True
            or seal_check.get("company_conflict") is True
            or not str(seal_check.get("recognized") or "").strip()
        ):
            return {"accepted": False, "reason": "不满足视觉参考章复核前提"}
        requirement = normalize_text(str(seal_check.get("requirement") or ""))
        if len(requirement) < 8:
            return {"accepted": False, "reason": "签章要求过短"}
        with self._lock:
            references = list(self.references.get(requirement, []))
        filename = str(result.get("filename") or "")
        references = [item for item in references if item.filename != filename]
        if not references:
            return {"accepted": False, "reason": "没有同签章要求的人工真值阳性参考章"}

        best: dict | None = None
        all_evidence: list[dict] = []
        for artifact in result.get("processing_artifacts", {}).get("seals", []):
            candidate_url = str(artifact.get("color_isolated_url") or "")
            candidate_path = self._artifact_path(candidate_url)
            if candidate_path is None:
                continue
            for reference in references:
                metrics = self._compare(candidate_path, reference.path)
                evidence = {
                    **metrics,
                    "candidate_index": int(artifact.get("index", -1)),
                    "candidate_url": candidate_url,
                    "reference_filename": reference.filename,
                    "reference_url": reference.artifact_url,
                    "requirement": str(seal_check.get("requirement") or ""),
                }
                all_evidence.append(evidence)
                if best is None or _evidence_rank(evidence) > _evidence_rank(best):
                    best = evidence
        if best is None:
            return {"accepted": False, "reason": "没有可读取的章色分离图"}
        strict_accepted = _passes_strict_gate(best)
        high_ratio_accepted = _passes_high_ratio_gate(best)
        high_support_accepted = _passes_high_support_gate(best)
        consensus = _best_consensus(all_evidence)
        consensus_accepted = bool(consensus.get("accepted"))
        if consensus_accepted and not (
            strict_accepted or high_ratio_accepted or high_support_accepted
        ):
            consensus_candidate = int(consensus["candidate_index"])
            leading_reference = str(
                consensus["matches"][0]["reference_filename"]
            )
            best = max(
                (
                    item for item in all_evidence
                    if int(item.get("candidate_index", -1))
                    == consensus_candidate
                    and str(item.get("reference_filename") or "")
                    == leading_reference
                ),
                key=_evidence_rank,
            )
        accepted = (
            strict_accepted or high_ratio_accepted
            or high_support_accepted or consensus_accepted
        )
        route = (
            "strict_single_reference" if strict_accepted
            else "high_ratio_single_reference" if high_ratio_accepted
            else "high_support_minor_coverage" if high_support_accepted
            else "multi_reference_consensus" if consensus_accepted
            else "rejected"
        )
        best["accepted"] = accepted
        best["route"] = route
        best["consensus_candidate_index"] = consensus.get(
            "candidate_index", -1
        )
        best["consensus_reference_count"] = int(
            consensus.get("reference_count", 0)
        )
        best["consensus_matches"] = consensus.get("matches", [])
        best["thresholds"] = {
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
            "consensus": {
                "distinct_references": CONSENSUS_MIN_DISTINCT_REFERENCES,
                "top_inliers": CONSENSUS_MIN_TOP_INLIERS,
                "good_matches": CONSENSUS_MIN_GOOD_MATCHES,
                "homography_inliers": CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": CONSENSUS_MIN_INLIER_RATIO,
                "surface_coverage": CONSENSUS_MIN_SURFACE_COVERAGE,
            },
        }
        best["confidence"] = (
            _reference_confidence(best, route, consensus)
            if accepted else 0.0
        )
        best["reason"] = (
            (
                "与同签章要求的人工真值阳性章形成大面积几何一致"
                if strict_accepted else
                "与人工真值阳性章形成高内点率的大面积几何一致"
                if high_ratio_accepted else
                "与人工真值阳性章形成高支持度几何一致，覆盖差异仅为裁剪抖动"
                if high_support_accepted else
                "同一章区与至少两份独立人工真值阳性章形成几何一致"
            )
            if accepted else
            "视觉特征未达到人工真值参考章的严格几何门槛"
        )
        return best

    def _artifact_path(self, url: str) -> Path | None:
        match = re.fullmatch(r"/files/artifacts/(.+)", url)
        if not match:
            return None
        candidate = (self.artifact_root / match.group(1)).resolve()
        try:
            candidate.relative_to(self.artifact_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _compare(self, candidate: Path, reference: Path) -> dict:
        with self._lock:
            candidate_keypoints, candidate_descriptors, candidate_shape = (
                self._descriptors(candidate)
            )
            reference_keypoints, reference_descriptors, reference_shape = (
                self._descriptors(reference)
            )
            if (
                candidate_descriptors is None
                or reference_descriptors is None
                or len(candidate_keypoints) < 8
                or len(reference_keypoints) < 8
            ):
                return _empty_metrics()
            pairs = self._matcher.knnMatch(
                candidate_descriptors, reference_descriptors, k=2
            )
        good = [
            first for first, second in pairs
            if first.distance < 0.72 * second.distance
        ]
        if len(good) < 6:
            return {**_empty_metrics(), "good_matches": len(good)}
        candidate_points = np.float32([
            candidate_keypoints[item.queryIdx].pt for item in good
        ])
        reference_points = np.float32([
            reference_keypoints[item.trainIdx].pt for item in good
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
                _point_coverage(candidate_points[inlier_mask], candidate_shape), 4
            ),
            "reference_coverage": round(
                _point_coverage(reference_points[inlier_mask], reference_shape), 4
            ),
        }

    def _descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        key = str(path)
        cached = self._descriptor_cache.get(key)
        if cached is not None:
            return cached
        image = cv2.imdecode(
            np.fromfile(key, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
        )
        if image is None:
            value = ([], None, (1, 1))
            self._descriptor_cache[key] = value
            return value
        ys, xs = np.where(image < 245)
        if len(xs) > 30:
            pad = 8
            x1, x2 = max(0, int(xs.min()) - pad), min(
                image.shape[1], int(xs.max()) + pad + 1
            )
            y1, y2 = max(0, int(ys.min()) - pad), min(
                image.shape[0], int(ys.max()) + pad + 1
            )
            image = image[y1:y2, x1:x2]
        scale = 800 / max(1, max(image.shape))
        if scale < 1.0 or scale > 1.2:
            image = cv2.resize(
                image,
                None,
                fx=scale,
                fy=scale,
                interpolation=(
                    cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                ),
            )
        keypoints, descriptors = self._sift.detectAndCompute(image, None)
        value = (keypoints or [], descriptors, image.shape[:2])
        self._descriptor_cache[key] = value
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


def _evidence_rank(evidence: dict) -> tuple:
    return (
        int(evidence.get("homography_inliers", 0)),
        min(
            float(evidence.get("candidate_coverage", 0)),
            float(evidence.get("reference_coverage", 0)),
        ),
        int(evidence.get("good_matches", 0)),
    )


def _passes_strict_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= MIN_GOOD_MATCHES
        and evidence["homography_inliers"] >= MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= MIN_INLIER_RATIO
        and evidence["candidate_coverage"] >= MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"] >= MIN_SURFACE_COVERAGE
    )


def _passes_high_ratio_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= HIGH_RATIO_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= HIGH_RATIO_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_RATIO_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_RATIO_MIN_SURFACE_COVERAGE
    )


def _passes_high_support_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= HIGH_SUPPORT_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= HIGH_SUPPORT_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_SUPPORT_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_SUPPORT_MIN_SURFACE_COVERAGE
    )


def _passes_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"] >= CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _best_consensus(all_evidence: list[dict]) -> dict:
    """Return the strongest same-region, distinct-file consensus group."""
    by_candidate: dict[int, dict[str, dict]] = defaultdict(dict)
    for evidence in all_evidence:
        if not _passes_consensus_vote(evidence):
            continue
        candidate_index = int(evidence.get("candidate_index", -1))
        reference_filename = str(evidence.get("reference_filename") or "")
        if candidate_index < 0 or not reference_filename:
            continue
        previous = by_candidate[candidate_index].get(reference_filename)
        if previous is None or _evidence_rank(evidence) > _evidence_rank(previous):
            by_candidate[candidate_index][reference_filename] = evidence

    groups: list[dict] = []
    for candidate_index, distinct in by_candidate.items():
        matches = sorted(
            distinct.values(), key=_evidence_rank, reverse=True
        )
        top_inliers = int(matches[0]["homography_inliers"]) if matches else 0
        groups.append({
            "candidate_index": candidate_index,
            "reference_count": len(matches),
            "top_inliers": top_inliers,
            "accepted": bool(
                len(matches) >= CONSENSUS_MIN_DISTINCT_REFERENCES
                and top_inliers >= CONSENSUS_MIN_TOP_INLIERS
            ),
            "matches": [
                {
                    key: item[key]
                    for key in (
                        "reference_filename", "reference_url", "good_matches",
                        "homography_inliers", "inlier_ratio",
                        "candidate_coverage", "reference_coverage",
                    )
                }
                for item in matches
            ],
        })
    if not groups:
        return {
            "accepted": False,
            "candidate_index": -1,
            "reference_count": 0,
            "top_inliers": 0,
            "matches": [],
        }
    return max(
        groups,
        key=lambda item: (
            bool(item["accepted"]),
            int(item["reference_count"]),
            int(item["top_inliers"]),
            sum(int(row["homography_inliers"]) for row in item["matches"]),
        ),
    )


def _reference_confidence(
    evidence: dict, route: str, consensus: dict | None = None
) -> float:
    # Passing the strict gate is already strong human-reference evidence.
    # The remaining margin raises confidence gradually without ever claiming
    # certainty from a visual template alone.
    inlier_margin = min(
        1.0,
        (int(evidence["homography_inliers"]) - MIN_HOMOGRAPHY_INLIERS) / 120,
    )
    ratio_margin = min(
        1.0,
        (float(evidence["inlier_ratio"]) - MIN_INLIER_RATIO) / 0.25,
    )
    coverage_margin = min(
        1.0,
        (
            min(
                float(evidence["candidate_coverage"]),
                float(evidence["reference_coverage"]),
            )
            - MIN_SURFACE_COVERAGE
        ) / 0.35,
    )
    confidence = round(
        min(0.98, 0.90 + 0.04 * inlier_margin + 0.02 * ratio_margin
            + 0.02 * coverage_margin),
        3,
    )
    if route == "multi_reference_consensus":
        # Consensus has independent-file corroboration, but its leading match
        # is intentionally below the strict single-reference boundary.
        second_inliers = int(
            ((consensus or {}).get("matches") or [{}, {}])[1].get(
                "homography_inliers", CONSENSUS_MIN_HOMOGRAPHY_INLIERS
            )
        ) if len((consensus or {}).get("matches") or []) >= 2 else 0
        confidence = min(
            0.94,
            0.91 + 0.01 * min(
                3.0,
                max(0, second_inliers - CONSENSUS_MIN_HOMOGRAPHY_INLIERS) / 5,
            ),
        )
    elif route == "high_support_minor_coverage":
        confidence = min(
            0.96,
            0.92
            + 0.02 * min(
                1.0,
                max(
                    0,
                    int(evidence["homography_inliers"])
                    - HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
                ) / 60,
            )
            + 0.01 * min(
                1.0,
                max(
                    0.0,
                    float(evidence["inlier_ratio"])
                    - HIGH_SUPPORT_MIN_INLIER_RATIO,
                ) / 0.20,
            ),
        )
    return round(confidence, 3)
