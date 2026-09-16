"""Reference gallery and matching entry point; legacy rule exports remain available."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .parser import normalize_text
from .seal_reference_confidence import (
    _reference_confidence,
)
from .seal_reference_consensus import (
    _best_chromatic_crop_consensus,
    _best_color_mask_consensus,
    _best_consensus,
    _best_consensus_group,
    _best_high_purity_consensus,
    _best_prefix_color_mask_consensus,
)
from .seal_reference_constants import (
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO,
    BARE_COMPANY_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_CORRELATION,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_DICE,
    BARE_COMPANY_ONE_GLYPH_MIN_COLOR_MASK_SCORE,
    BARE_COMPANY_ONE_GLYPH_MIN_COMPANY_SCORE,
    BARE_COMPANY_ONE_GLYPH_MIN_GOOD_MATCHES,
    BARE_COMPANY_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    BARE_COMPANY_ONE_GLYPH_MIN_INLIER_RATIO,
    BARE_COMPANY_ONE_GLYPH_MIN_SURFACE_COVERAGE,
    BRANDED_STATION_MIN_GOOD_MATCHES,
    BRANDED_STATION_MIN_HOMOGRAPHY_INLIERS,
    BRANDED_STATION_MIN_INLIER_RATIO,
    BRANDED_STATION_MIN_SURFACE_COVERAGE,
    CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES,
    CHROMATIC_CONSENSUS_MIN_GOOD_MATCHES,
    CHROMATIC_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    CHROMATIC_CONSENSUS_MIN_INLIER_RATIO,
    CHROMATIC_CONSENSUS_MIN_SURFACE_COVERAGE,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO,
    CLIPPED_COMPANY_REFERENCE_MAX_MISSING_PREFIX,
    CLIPPED_COMPANY_REFERENCE_MIN_CANDIDATE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_CANDIDATE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_INLIER_RATIO,
    CLIPPED_COMPANY_REFERENCE_MIN_CHROMATIC_REFERENCE_COVERAGE,
    CLIPPED_COMPANY_REFERENCE_MIN_COMPANY_SCORE,
    CLIPPED_COMPANY_REFERENCE_MIN_GOOD_MATCHES,
    CLIPPED_COMPANY_REFERENCE_MIN_HOMOGRAPHY_INLIERS,
    CLIPPED_COMPANY_REFERENCE_MIN_INLIER_RATIO,
    CLIPPED_COMPANY_REFERENCE_MIN_RECOGNIZED_LENGTH,
    CLIPPED_COMPANY_REFERENCE_MIN_REFERENCE_COVERAGE,
    COLOR_MASK_CANVAS_SIZE,
    COLOR_MASK_CONSENSUS_MIN_CANDIDATE_COVERAGE,
    COLOR_MASK_CONSENSUS_MIN_COMPANY_SCORE,
    COLOR_MASK_CONSENSUS_MIN_CORRELATION,
    COLOR_MASK_CONSENSUS_MIN_DICE,
    COLOR_MASK_CONSENSUS_MIN_DISTINCT_REFERENCES,
    COLOR_MASK_CONSENSUS_MIN_GOOD_MATCHES,
    COLOR_MASK_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    COLOR_MASK_CONSENSUS_MIN_RECOGNIZED_CHARS,
    COLOR_MASK_CONSENSUS_MIN_REFERENCE_COVERAGE,
    COLOR_MASK_CONSENSUS_MIN_SCORE,
    COLOR_MASK_CONSENSUS_MIN_TOP_SCORE,
    COLOR_MASK_MIN_CORRELATION,
    COLOR_MASK_MIN_DICE,
    COLOR_MASK_MIN_SCORE,
    COLOR_MASK_ROTATIONS,
    COLOR_SIFT_MIN_CORRELATION,
    COLOR_SIFT_MIN_DICE,
    COLOR_SIFT_MIN_GOOD_MATCHES,
    COLOR_SIFT_MIN_HOMOGRAPHY_INLIERS,
    COLOR_SIFT_MIN_INLIER_RATIO,
    COLOR_SIFT_MIN_SCORE,
    COLOR_SIFT_MIN_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_GOOD_MATCHES,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_INLIER_RATIO,
    COMPANY_CONFLICT_REFERENCE_MIN_CHROMATIC_SURFACE_COVERAGE,
    COMPANY_CONFLICT_REFERENCE_MIN_COMPANY_SCORE,
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
    HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES,
    HIGH_PURITY_CONSENSUS_MIN_GOOD_MATCHES,
    HIGH_PURITY_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    HIGH_PURITY_CONSENSUS_MIN_INLIER_RATIO,
    HIGH_PURITY_CONSENSUS_MIN_SURFACE_COVERAGE,
    HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
    HIGH_RATIO_MIN_GOOD_MATCHES,
    HIGH_RATIO_MIN_HOMOGRAPHY_INLIERS,
    HIGH_RATIO_MIN_INLIER_RATIO,
    HIGH_RATIO_MIN_SURFACE_COVERAGE,
    HIGH_SUPPORT_MIN_GOOD_MATCHES,
    HIGH_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    HIGH_SUPPORT_MIN_INLIER_RATIO,
    HIGH_SUPPORT_MIN_SURFACE_COVERAGE,
    MIN_GOOD_MATCHES,
    MIN_HOMOGRAPHY_INLIERS,
    MIN_INLIER_RATIO,
    MIN_SURFACE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_CANDIDATE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_COMPANY_SCORE,
    PREFIX_COLOR_CONSENSUS_MIN_CORRELATION,
    PREFIX_COLOR_CONSENSUS_MIN_DICE,
    PREFIX_COLOR_CONSENSUS_MIN_DISTINCT_REFERENCES,
    PREFIX_COLOR_CONSENSUS_MIN_GOOD_MATCHES,
    PREFIX_COLOR_CONSENSUS_MIN_HOMOGRAPHY_INLIERS,
    PREFIX_COLOR_CONSENSUS_MIN_INLIER_RATIO,
    PREFIX_COLOR_CONSENSUS_MIN_PREFIX_CHARS,
    PREFIX_COLOR_CONSENSUS_MIN_REFERENCE_COVERAGE,
    PREFIX_COLOR_CONSENSUS_MIN_SCORE,
    PREFIX_COLOR_CONSENSUS_MIN_TOP_SCORE,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_GOOD_MATCHES,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_HOMOGRAPHY_INLIERS,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_INLIER_RATIO,
    RECEIVING_ONE_GLYPH_MIN_CHROMATIC_SURFACE_COVERAGE,
    RECEIVING_ONE_GLYPH_MIN_COMPANY_SCORE,
    RECEIVING_ONE_GLYPH_MIN_GOOD_MATCHES,
    RECEIVING_ONE_GLYPH_MIN_HOMOGRAPHY_INLIERS,
    RECEIVING_ONE_GLYPH_MIN_INLIER_RATIO,
    RECEIVING_ONE_GLYPH_MIN_SURFACE_COVERAGE,
    TRIMMED_CHROMATIC_MIN_GOOD_MATCHES,
    TRIMMED_CHROMATIC_MIN_HOMOGRAPHY_INLIERS,
    TRIMMED_CHROMATIC_MIN_INLIER_RATIO,
    TRIMMED_CHROMATIC_MIN_SURFACE_COVERAGE,
    TRIMMED_CHROMATIC_QUANTILE,
    ULTRA_SUPPORT_MIN_GOOD_MATCHES,
    ULTRA_SUPPORT_MIN_HOMOGRAPHY_INLIERS,
    ULTRA_SUPPORT_MIN_INLIER_RATIO,
    ULTRA_SUPPORT_MIN_SURFACE_COVERAGE,
)
from .seal_reference_decision import (
    _decide_reference_match,
)
from .seal_reference_gates import (
    _color_evidence_rank,
    _evidence_rank,
    _has_exactly_one_company_glyph_substitution,
    _has_independent_short_company_fragment,
    _is_bare_company_requirement,
    _longest_common_substring_length,
    _passes_bare_company_one_glyph_reference_gate,
    _passes_branded_station_gate,
    _passes_chromatic_crop_consensus_vote,
    _passes_clipped_company_reference_gate,
    _passes_color_mask_consensus_vote,
    _passes_color_mask_gate,
    _passes_color_sift_gate,
    _passes_company_conflict_reference_gate,
    _passes_consensus_vote,
    _passes_high_purity_consensus_vote,
    _passes_high_ratio_gate,
    _passes_high_support_gate,
    _passes_prefix_color_mask_consensus_vote,
    _passes_receiving_one_glyph_reference_gate,
    _passes_strict_gate,
    _passes_trimmed_chromatic_gate,
    _passes_ultra_support_gate,
    _recognized_requirement_prefix_length,
    _recognized_requirement_subsequence_length,
    _trimmed_chromatic_evidence_rank,
)
from .seal_reference_geometry import (
    SealReferenceGeometry,
    _chromatic_crop_image,
    _color_mask_similarity,
    _empty_metrics,
    _normalized_color_ink,
    _point_coverage,
    _shift_mask,
    _trimmed_chromatic_crop_image,
)


@dataclass(frozen=True)
class SealReference:
    filename: str
    requirement: str
    artifact_url: str
    path: Path


class SealReferenceMatcher(SealReferenceGeometry):
    """Match a new color-isolated seal to human-confirmed local references.

    OCR remains the primary evidence.  This matcher is a conservative second
    route for repeated customer stamps: references must come from a positive
    ground-truth row, the normalized signature requirement must be identical,
    and local feature matches must form a broad homography over both stamp
    surfaces.  A short logo/star match can never promote a result. Conflicting
    OCR company names remain blocked except for the explicit ultra-strong
    same-requirement 售后章 route documented above.
    """

    def __init__(self, artifact_root: str | Path) -> None:
        super().__init__(artifact_root)
        self.references: dict[str, list[SealReference]] = {}
        self.truth_filenames: set[str] = set()

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
            self._trimmed_chromatic_descriptor_cache = {
                key: value
                for key, value in self._trimmed_chromatic_descriptor_cache.items()
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
        best, best_trimmed_chromatic, best_color, all_evidence = (
            self._collect_evidence(result, seal_check, references)
        )
        if best is None:
            return {"accepted": False, "reason": "没有可读取的章色分离图"}
        return _decide_reference_match(
            seal_check, requirement, best, best_trimmed_chromatic,
            best_color, all_evidence,
        )

    def _collect_evidence(
        self, result: dict, seal_check: dict, references: list[SealReference],
    ) -> tuple[dict | None, dict | None, dict | None, list[dict]]:
        best: dict | None = None
        best_trimmed_chromatic: dict | None = None
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
            candidate_trimmed_chromatic_crop_url = (
                self._write_trimmed_chromatic_crop_artifact(
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
                trimmed_chromatic_metrics = {
                    f"trimmed_chromatic_{key}": value
                    for key, value in self._compare_trimmed_chromatic_crop(
                        candidate_path, reference.path
                    ).items()
                }
                color_metrics = self._compare_color_mask(
                    candidate_path, reference.path
                )
                evidence = {
                    **metrics,
                    **chromatic_metrics,
                    **trimmed_chromatic_metrics,
                    **color_metrics,
                    "candidate_index": int(artifact.get("index", -1)),
                    "candidate_url": candidate_url,
                    "candidate_chromatic_crop_url": (
                        candidate_chromatic_crop_url
                    ),
                    "candidate_trimmed_chromatic_crop_url": (
                        candidate_trimmed_chromatic_crop_url
                    ),
                    "reference_filename": reference.filename,
                    "reference_url": reference.artifact_url,
                    "requirement": str(seal_check.get("requirement") or ""),
                }
                all_evidence.append(evidence)
                if best is None or _evidence_rank(evidence) > _evidence_rank(best):
                    best = evidence
                if (
                    best_trimmed_chromatic is None
                    or _trimmed_chromatic_evidence_rank(evidence)
                    > _trimmed_chromatic_evidence_rank(
                        best_trimmed_chromatic
                    )
                ):
                    best_trimmed_chromatic = evidence
                if (
                    best_color is None
                    or _color_evidence_rank(evidence)
                    > _color_evidence_rank(best_color)
                ):
                    best_color = evidence
        return best, best_trimmed_chromatic, best_color, all_evidence
