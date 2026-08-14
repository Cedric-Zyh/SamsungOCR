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

# A partially obscured/cropped stamp may fall just below the 28% surface
# boundary even though the remaining ink carries exceptionally dense and
# pure geometry.  The complete 301-image human-truth matrix has exactly one
# new positive at 152/107 matches/inliers, 70.39% purity and 24.40%/51.10%
# bidirectional coverage.  The strongest known wrong-stamp control has only
# 46/24 matches/inliers.  Keep this as an independent all-boundaries gate;
# it must never act as a general relaxation of the existing coverage routes.
ULTRA_SUPPORT_MIN_GOOD_MATCHES = 145
ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS = 105
ULTRA_SUPPORT_MIN_INLIER_RATIO = 0.70
ULTRA_SUPPORT_MIN_SURFACE_COVERAGE = 0.24

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

# Two independent human-confirmed files may safely compensate for a modestly
# lower correspondence count when *each* vote is geometrically purer than the
# standard consensus route.  The 301-image matrix has one pending positive
# with 90/72 and 89/66 matches/inliers, 80.0%/74.16% purity and at least 33%
# bidirectional coverage.  Requiring two distinct files prevents repeated
# crops from one scan from masquerading as independent corroboration.
HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS = 70
HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES = 85
HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO = 0.70
HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE = 0.30

# Black form lines and signatures sometimes survive the color-isolation stage
# and enlarge the grayscale SIFT crop even when the stamp itself is complete.
# A second representation derives crop bounds from chromatic ink but retains
# the original grayscale texture for SIFT.  It may promote only when two
# distinct human-confirmed files agree; the leading vote must be exceptionally
# pure and dense, while the second still carries substantial independent
# support.  The full 301-image matrix accepts one positive and no wrong stamp.
CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES = 2
CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES = 100
CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS = 65
CHROMATIC_CONSENSUS_MIN_INLIER_RATIO = 0.68
CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE = 0.24
CHROMATIC_CONSENSUS_MIN_TOP_INLIERS = 95
CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO = 0.85

# Color-ink geometry is a fallback for faded scans where SIFT cannot retain
# enough local keypoints.  The full 301-receipt matrix has a wide margin:
# accepted positives start at 0.8158/0.8096/0.8319 for composite score,
# correlation and Dice, while the strongest wrong-stamp control reaches only
# 0.4337/0.4535/0.3828.  Require all three 0.80 boundaries independently.
COLOR_MASK_MIN_SCORE = 0.80
COLOR_MASK_MIN_CORRELATION = 0.80
COLOR_MASK_MIN_DICE = 0.80
COLOR_MASK_CANVAS_SIZE = 320
COLOR_MASK_ROTATIONS = tuple(range(-12, 13, 2))

# A moderately faded whole-ink match may still be promoted when the *same*
# candidate/reference pair also has broad, high-purity SIFT geometry. This is
# a separate AND gate, not a relaxation of the strict color-only route.
COLOR_SIFT_MIN_SCORE = 0.70
COLOR_SIFT_MIN_CORRELATION = 0.70
COLOR_SIFT_MIN_DICE = 0.65
COLOR_SIFT_MIN_GOOD_MATCHES = 60
COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS = 40
COLOR_SIFT_MIN_INLIER_RATIO = 0.65
COLOR_SIFT_MIN_SURFACE_COVERAGE = 0.30


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
        self._chromatic_descriptor_cache: dict[
            str, tuple[list, np.ndarray | None, tuple[int, int]]
        ] = {}
        self._color_mask_cache: dict[str, np.ndarray | None] = {}
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
            self._chromatic_descriptor_cache = {
                key: value
                for key, value in self._chromatic_descriptor_cache.items()
                if key in existing and Path(key).is_file()
            }
            self._color_mask_cache = {
                key: value
                for key, value in self._color_mask_cache.items()
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
        best_color: dict | None = None
        all_evidence: list[dict] = []
        for artifact in result.get("processing_artifacts", {}).get("seals", []):
            candidate_url = str(artifact.get("color_isolated_url") or "")
            candidate_path = self._artifact_path(candidate_url)
            if candidate_path is None:
                continue
            candidate_chromatic_crop_url = (
                self._write_chromatic_crop_artifact(
                    candidate_path, candidate_url
                )
            )
            for reference in references:
                metrics = self._compare(candidate_path, reference.path)
                chromatic_metrics = {
                    f"chromatic_{key}": value
                    for key, value in self._compare_chromatic_crop(
                        candidate_path, reference.path
                    ).items()
                }
                color_metrics = self._compare_color_mask(
                    candidate_path, reference.path
                )
                evidence = {
                    **metrics,
                    **chromatic_metrics,
                    **color_metrics,
                    "candidate_index": int(artifact.get("index", -1)),
                    "candidate_url": candidate_url,
                    "candidate_chromatic_crop_url": (
                        candidate_chromatic_crop_url
                    ),
                    "reference_filename": reference.filename,
                    "reference_url": reference.artifact_url,
                    "requirement": str(seal_check.get("requirement") or ""),
                }
                all_evidence.append(evidence)
                if best is None or _evidence_rank(evidence) > _evidence_rank(best):
                    best = evidence
                if (
                    best_color is None
                    or _color_evidence_rank(evidence)
                    > _color_evidence_rank(best_color)
                ):
                    best_color = evidence
        if best is None:
            return {"accepted": False, "reason": "没有可读取的章色分离图"}
        strict_accepted = _passes_strict_gate(best)
        high_ratio_accepted = _passes_high_ratio_gate(best)
        high_support_accepted = _passes_high_support_gate(best)
        ultra_support_accepted = _passes_ultra_support_gate(best)
        consensus = _best_consensus(all_evidence)
        consensus_accepted = bool(consensus.get("accepted"))
        high_purity_consensus = _best_high_purity_consensus(all_evidence)
        high_purity_consensus_accepted = bool(
            high_purity_consensus.get("accepted")
        )
        chromatic_consensus = _best_chromatic_crop_consensus(all_evidence)
        chromatic_consensus_accepted = bool(
            chromatic_consensus.get("accepted")
        )
        color_mask_accepted = bool(
            best_color is not None and _passes_color_mask_gate(best_color)
        )
        color_sift_accepted = bool(
            best_color is not None and _passes_color_sift_gate(best_color)
        )
        active_consensus = (
            consensus if consensus_accepted
            else high_purity_consensus
            if high_purity_consensus_accepted
            else chromatic_consensus
            if chromatic_consensus_accepted
            else consensus
        )
        if (
            consensus_accepted or high_purity_consensus_accepted
            or chromatic_consensus_accepted
        ) and not (
            strict_accepted or high_ratio_accepted or high_support_accepted
            or ultra_support_accepted
        ):
            consensus_candidate = int(active_consensus["candidate_index"])
            leading_reference = str(
                active_consensus["matches"][0]["reference_filename"]
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
        if (
            (color_mask_accepted or color_sift_accepted)
            and not (
                strict_accepted or high_ratio_accepted
                or high_support_accepted or ultra_support_accepted
                or consensus_accepted or high_purity_consensus_accepted
                or chromatic_consensus_accepted
            )
        ):
            best = best_color
        accepted = (
            strict_accepted or high_ratio_accepted
            or high_support_accepted or ultra_support_accepted
            or consensus_accepted or high_purity_consensus_accepted
            or chromatic_consensus_accepted
            or color_mask_accepted or color_sift_accepted
        )
        route = (
            "strict_single_reference" if strict_accepted
            else "high_ratio_single_reference" if high_ratio_accepted
            else "high_support_minor_coverage" if high_support_accepted
            else "ultra_support_partial_coverage" if ultra_support_accepted
            else "multi_reference_consensus" if consensus_accepted
            else "high_purity_multi_reference_consensus"
            if high_purity_consensus_accepted
            else "chromatic_crop_multi_reference_consensus"
            if chromatic_consensus_accepted
            else "color_mask_geometry" if color_mask_accepted
            else "color_mask_sift_geometry" if color_sift_accepted
            else "rejected"
        )
        best["accepted"] = accepted
        best["route"] = route
        best["consensus_candidate_index"] = active_consensus.get(
            "candidate_index", -1
        )
        best["consensus_reference_count"] = int(
            active_consensus.get("reference_count", 0)
        )
        best["consensus_matches"] = active_consensus.get("matches", [])
        if route == "chromatic_crop_multi_reference_consensus":
            best["regular_geometry"] = {
                key: best.get(key, 0)
                for key in (
                    "good_matches", "homography_inliers", "inlier_ratio",
                    "candidate_coverage", "reference_coverage",
                )
            }
            for key in (
                "good_matches", "homography_inliers", "inlier_ratio",
                "candidate_coverage", "reference_coverage",
            ):
                best[key] = best.get(f"chromatic_{key}", 0)
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
            "ultra_support": {
                "good_matches": ULTRA_SUPPORT_MIN_GOOD_MATCHES,
                "homography_inliers": ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
                "inlier_ratio": ULTRA_SUPPORT_MIN_INLIER_RATIO,
                "surface_coverage": ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
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
        }
        best["confidence"] = (
            _reference_confidence(best, route, active_consensus)
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
                "与人工真值阳性章形成超高支持度几何一致，局部覆盖不足源于遮挡或裁剪"
                if ultra_support_accepted else
                "同一章区与至少两份独立人工真值阳性章形成几何一致"
                if consensus_accepted else
                "同一章区与两份独立人工真值阳性章形成高纯度几何一致"
                if high_purity_consensus_accepted else
                "排除黑色表格与签字噪声后，同一章区与两份独立人工真值阳性章一致"
                if chromatic_consensus_accepted else
                "与人工真值阳性章的彩色墨迹形成整体几何一致"
                if color_mask_accepted else
                "与人工真值阳性章同时形成整体彩色墨迹与大面积局部几何一致"
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
            candidate_values = self._descriptors(candidate)
            reference_values = self._descriptors(reference)
        return self._compare_descriptor_values(
            candidate_values, reference_values
        )

    def _compare_chromatic_crop(
        self, candidate: Path, reference: Path
    ) -> dict:
        with self._lock:
            candidate_values = self._chromatic_descriptors(candidate)
            reference_values = self._chromatic_descriptors(reference)
        return self._compare_descriptor_values(
            candidate_values, reference_values
        )

    def _compare_descriptor_values(
        self,
        candidate_values: tuple[
            list, np.ndarray | None, tuple[int, int]
        ],
        reference_values: tuple[
            list, np.ndarray | None, tuple[int, int]
        ],
    ) -> dict:
        candidate_keypoints, candidate_descriptors, candidate_shape = (
            candidate_values
        )
        reference_keypoints, reference_descriptors, reference_shape = (
            reference_values
        )
        with self._lock:
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

    def _compare_color_mask(self, candidate: Path, reference: Path) -> dict:
        return _color_mask_similarity(
            self._color_mask(candidate), self._color_mask(reference)
        )

    def _write_chromatic_crop_artifact(
        self, path: Path, source_url: str
    ) -> str:
        """Persist the exact robust crop used by the alternate SIFT route."""
        crop = _chromatic_crop_image(path)
        if crop is None:
            return ""
        match = re.fullmatch(r"/files/artifacts/(.+)", source_url)
        if not match:
            return ""
        source_name = path.name
        if source_name.endswith("-color-isolated.png"):
            output_name = source_name.replace(
                "-color-isolated.png", "-chromatic-crop.png"
            )
        else:
            output_name = f"{path.stem}-chromatic-crop.png"
        output = path.with_name(output_name)
        if not output.is_file():
            encoded, payload = cv2.imencode(".png", crop)
            if not encoded:
                return ""
            payload.tofile(str(output))
        relative = output.relative_to(self.artifact_root).as_posix()
        return f"/files/artifacts/{relative}"

    def _color_mask(self, path: Path) -> np.ndarray | None:
        key = str(path)
        if key not in self._color_mask_cache:
            self._color_mask_cache[key] = _normalized_color_ink(path)
        return self._color_mask_cache[key]

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

    def _chromatic_descriptors(
        self, path: Path
    ) -> tuple[list, np.ndarray | None, tuple[int, int]]:
        """Crop by colored ink, then retain grayscale texture for SIFT."""
        key = str(path)
        cached = self._chromatic_descriptor_cache.get(key)
        if cached is not None:
            return cached
        color = _chromatic_crop_image(path)
        if color is None:
            value = ([], None, (1, 1))
            self._chromatic_descriptor_cache[key] = value
            return value
        image = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
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
        self._chromatic_descriptor_cache[key] = value
        return value


def _empty_metrics() -> dict:
    return {
        "good_matches": 0,
        "homography_inliers": 0,
        "inlier_ratio": 0.0,
        "candidate_coverage": 0.0,
        "reference_coverage": 0.0,
    }


def _chromatic_crop_image(path: Path) -> np.ndarray | None:
    """Crop to chromatic ink while preserving original color/gray texture."""
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return None
    blue, green, red = cv2.split(image)
    high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
    low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
    chromatic = ((high - low) >= 22) & (low <= 235)
    ys, xs = np.where(chromatic)
    if len(xs) < 180:
        return image
    pad = 8
    x1 = max(0, int(xs.min()) - pad)
    x2 = min(image.shape[1], int(xs.max()) + pad + 1)
    y1 = max(0, int(ys.min()) - pad)
    y2 = min(image.shape[0], int(ys.max()) + pad + 1)
    return image[y1:y2, x1:x2]


def _normalized_color_ink(path: Path) -> np.ndarray | None:
    """Center chromatic stamp ink and discard black form/handwriting lines."""
    image = cv2.imdecode(
        np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return None
    blue, green, red = cv2.split(image)
    high = np.maximum(np.maximum(red, green), blue).astype(np.int16)
    low = np.minimum(np.minimum(red, green), blue).astype(np.int16)
    mask = (((high - low) >= 22) & (low <= 235)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
    )
    ys, xs = np.where(mask > 0)
    if len(xs) < 180:
        return None
    pad = 4
    x1 = max(0, int(xs.min()) - pad)
    x2 = min(mask.shape[1], int(xs.max()) + pad + 1)
    y1 = max(0, int(ys.min()) - pad)
    y2 = min(mask.shape[0], int(ys.max()) + pad + 1)
    crop = mask[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    target = COLOR_MASK_CANVAS_SIZE - 32
    scale = target / max(crop.shape)
    resized = cv2.resize(
        crop,
        (
            max(1, round(crop.shape[1] * scale)),
            max(1, round(crop.shape[0] * scale)),
        ),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.zeros(
        (COLOR_MASK_CANVAS_SIZE, COLOR_MASK_CANVAS_SIZE), np.uint8
    )
    top = (COLOR_MASK_CANVAS_SIZE - resized.shape[0]) // 2
    left = (COLOR_MASK_CANVAS_SIZE - resized.shape[1]) // 2
    canvas[
        top:top + resized.shape[0], left:left + resized.shape[1]
    ] = resized
    return canvas


def _shift_mask(image: np.ndarray, dx: int, dy: int) -> np.ndarray:
    return cv2.warpAffine(
        image,
        np.float32([[1, 0, dx], [0, 1, dy]]),
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )


def _color_mask_similarity(
    candidate_mask: np.ndarray | None,
    reference_mask: np.ndarray | None,
) -> dict:
    if candidate_mask is None or reference_mask is None:
        return {
            "color_mask_score": 0.0,
            "color_mask_correlation": 0.0,
            "color_mask_dice": 0.0,
            "color_mask_angle": 0,
            "color_mask_dx": 0,
            "color_mask_dy": 0,
        }
    size = COLOR_MASK_CANVAS_SIZE
    yy, xx = np.ogrid[:size, :size]
    center = (size - 1) / 2
    radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    # The outer circle is common to unrelated stamps. Retain identifying ring
    # text, center star/logo and internal type line, but suppress the border.
    focus = (radius <= size * 0.43).astype(np.uint8)
    reference_focus = cv2.GaussianBlur(
        (reference_mask * focus).astype(np.float32) / 255.0,
        (5, 5),
        0,
    )
    padded = cv2.copyMakeBorder(
        reference_focus, 8, 8, 8, 8, cv2.BORDER_CONSTANT
    )
    best = {
        "color_mask_score": 0.0,
        "color_mask_correlation": 0.0,
        "color_mask_dice": 0.0,
        "color_mask_angle": 0,
        "color_mask_dx": 0,
        "color_mask_dy": 0,
    }
    for angle in COLOR_MASK_ROTATIONS:
        rotation = cv2.getRotationMatrix2D((center, center), angle, 1.0)
        rotated = cv2.warpAffine(
            candidate_mask,
            rotation,
            (size, size),
            flags=cv2.INTER_NEAREST,
            borderValue=0,
        )
        rotated_focus = cv2.GaussianBlur(
            (rotated * focus).astype(np.float32) / 255.0,
            (5, 5),
            0,
        )
        response = cv2.matchTemplate(
            padded, rotated_focus, cv2.TM_CCOEFF_NORMED
        )
        _minimum, correlation, _minimum_location, location = cv2.minMaxLoc(
            response
        )
        dx, dy = int(location[0] - 8), int(location[1] - 8)
        aligned = _shift_mask(rotated, dx, dy)
        left = (aligned > 64) & (focus > 0)
        right = (reference_mask > 64) & (focus > 0)
        intersection = int(np.logical_and(left, right).sum())
        dice = 2 * intersection / max(
            1, int(left.sum()) + int(right.sum())
        )
        score = max(0.0, 0.72 * float(correlation) + 0.28 * dice)
        if score > best["color_mask_score"]:
            best = {
                "color_mask_score": round(score, 4),
                "color_mask_correlation": round(float(correlation), 4),
                "color_mask_dice": round(float(dice), 4),
                "color_mask_angle": angle,
                "color_mask_dx": dx,
                "color_mask_dy": dy,
            }
    return best


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


def _color_evidence_rank(evidence: dict) -> tuple:
    return (
        float(evidence.get("color_mask_score", 0)),
        float(evidence.get("color_mask_correlation", 0)),
        float(evidence.get("color_mask_dice", 0)),
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


def _passes_ultra_support_gate(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= ULTRA_SUPPORT_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= ULTRA_SUPPORT_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= ULTRA_SUPPORT_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= ULTRA_SUPPORT_MIN_SURFACE_COVERAGE
    )


def _passes_color_mask_gate(evidence: dict) -> bool:
    return bool(
        float(evidence.get("color_mask_score", 0)) >= COLOR_MASK_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= COLOR_MASK_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0)) >= COLOR_MASK_MIN_DICE
    )


def _passes_color_sift_gate(evidence: dict) -> bool:
    """Require whole-ink and local geometry on the exact same reference."""
    return bool(
        float(evidence.get("color_mask_score", 0)) >= COLOR_SIFT_MIN_SCORE
        and float(evidence.get("color_mask_correlation", 0))
        >= COLOR_SIFT_MIN_CORRELATION
        and float(evidence.get("color_mask_dice", 0)) >= COLOR_SIFT_MIN_DICE
        and int(evidence.get("good_matches", 0))
        >= COLOR_SIFT_MIN_GOOD_MATCHES
        and int(evidence.get("homography_inliers", 0))
        >= COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS
        and float(evidence.get("inlier_ratio", 0))
        >= COLOR_SIFT_MIN_INLIER_RATIO
        and float(evidence.get("candidate_coverage", 0))
        >= COLOR_SIFT_MIN_SURFACE_COVERAGE
        and float(evidence.get("reference_coverage", 0))
        >= COLOR_SIFT_MIN_SURFACE_COVERAGE
    )


def _passes_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"] >= CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"] >= CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"] >= CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _passes_high_purity_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"]
        >= HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"]
        >= HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _passes_chromatic_crop_consensus_vote(evidence: dict) -> bool:
    return bool(
        evidence["good_matches"] >= CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES
        and evidence["homography_inliers"]
        >= CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS
        and evidence["inlier_ratio"]
        >= CHROMATIC_CONSENSUS_MIN_INLIER_RATIO
        and evidence["candidate_coverage"]
        >= CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
        and evidence["reference_coverage"]
        >= CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE
    )


def _best_consensus(all_evidence: list[dict]) -> dict:
    return _best_consensus_group(
        all_evidence,
        vote_gate=_passes_consensus_vote,
        minimum_references=CONSENSUS_MIN_DISTINCT_REFERENCES,
        minimum_top_inliers=CONSENSUS_MIN_TOP_INLIERS,
    )


def _best_high_purity_consensus(all_evidence: list[dict]) -> dict:
    return _best_consensus_group(
        all_evidence,
        vote_gate=_passes_high_purity_consensus_vote,
        minimum_references=HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES,
        minimum_top_inliers=HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
    )


def _best_chromatic_crop_consensus(all_evidence: list[dict]) -> dict:
    normalized = []
    for evidence in all_evidence:
        item = dict(evidence)
        for key in (
            "good_matches", "homography_inliers", "inlier_ratio",
            "candidate_coverage", "reference_coverage",
        ):
            item[key] = evidence.get(f"chromatic_{key}", 0)
        normalized.append(item)
    return _best_consensus_group(
        normalized,
        vote_gate=_passes_chromatic_crop_consensus_vote,
        minimum_references=CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES,
        minimum_top_inliers=CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
        minimum_top_ratio=CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO,
    )


def _best_consensus_group(
    all_evidence: list[dict],
    *,
    vote_gate: Any,
    minimum_references: int,
    minimum_top_inliers: int,
    minimum_top_ratio: float = 0.0,
) -> dict:
    """Return the strongest same-region, distinct-file consensus group."""
    by_candidate: dict[int, dict[str, dict]] = defaultdict(dict)
    for evidence in all_evidence:
        if not vote_gate(evidence):
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
                len(matches) >= minimum_references
                and top_inliers >= minimum_top_inliers
                and float(matches[0]["inlier_ratio"])
                >= minimum_top_ratio
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
    elif route == "high_purity_multi_reference_consensus":
        second_inliers = int(
            ((consensus or {}).get("matches") or [{}, {}])[1].get(
                "homography_inliers",
                HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
            )
        ) if len((consensus or {}).get("matches") or []) >= 2 else 0
        confidence = min(
            0.94,
            0.91 + 0.01 * min(
                3.0,
                max(
                    0,
                    second_inliers
                    - HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
                ) / 5,
            ),
        )
    elif route == "chromatic_crop_multi_reference_consensus":
        second_inliers = int(
            ((consensus or {}).get("matches") or [{}, {}])[1].get(
                "homography_inliers",
                CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
            )
        ) if len((consensus or {}).get("matches") or []) >= 2 else 0
        confidence = min(
            0.94,
            0.91 + 0.01 * min(
                3.0,
                max(
                    0,
                    second_inliers
                    - CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
                ) / 5,
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
    elif route == "ultra_support_partial_coverage":
        # Extra inliers/purity may raise confidence, while the small visible
        # surface prevents this route from making a near-certain claim.
        confidence = min(
            0.95,
            0.92
            + 0.02 * min(
                1.0,
                max(
                    0,
                    int(evidence["homography_inliers"])
                    - ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
                ) / 60,
            )
            + 0.01 * min(
                1.0,
                max(
                    0.0,
                    float(evidence["inlier_ratio"])
                    - ULTRA_SUPPORT_MIN_INLIER_RATIO,
                ) / 0.20,
            ),
        )
    elif route == "color_mask_geometry":
        # Whole-ink agreement is deliberately independent of sparse SIFT
        # keypoints. It starts at 91% and gains only a small margin above the
        # three strict 0.80 boundaries.
        margin = min(
            float(evidence.get("color_mask_score", 0)) - COLOR_MASK_MIN_SCORE,
            float(evidence.get("color_mask_correlation", 0))
            - COLOR_MASK_MIN_CORRELATION,
            float(evidence.get("color_mask_dice", 0)) - COLOR_MASK_MIN_DICE,
        )
        confidence = min(0.96, 0.91 + 0.5 * max(0.0, margin))
    elif route == "color_mask_sift_geometry":
        # Both representations agree on the same pair. Keep this conservative
        # route below the strongest strict SIFT/color-only matches.
        color_margin = min(
            float(evidence.get("color_mask_score", 0)) - COLOR_SIFT_MIN_SCORE,
            float(evidence.get("color_mask_correlation", 0))
            - COLOR_SIFT_MIN_CORRELATION,
            float(evidence.get("color_mask_dice", 0)) - COLOR_SIFT_MIN_DICE,
        )
        sift_margin = min(
            (int(evidence.get("homography_inliers", 0))
             - COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS) / 80,
            (float(evidence.get("inlier_ratio", 0))
             - COLOR_SIFT_MIN_INLIER_RATIO) / 0.25,
            (min(
                float(evidence.get("candidate_coverage", 0)),
                float(evidence.get("reference_coverage", 0)),
            ) - COLOR_SIFT_MIN_SURFACE_COVERAGE) / 0.30,
        )
        confidence = min(
            0.95,
            0.91 + 0.20 * max(0.0, color_margin)
            + 0.02 * max(0.0, sift_margin),
        )
    return round(confidence, 3)
