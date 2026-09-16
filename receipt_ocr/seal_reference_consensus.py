"""Independent-reference voting for visual seal evidence."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from .parser import normalize_text
from .seal_reference_constants import (
    CHROMATIC_CONSENSUS_MIN_DISTINCT_REFERENCES,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIERS,
    CHROMATIC_CONSENSUS_MIN_TOP_INLIER_RATIO,
    COLOR_MASK_CONSENSUS_MIN_COMPANY_SCORE,
    COLOR_MASK_CONSENSUS_MIN_DISTINCT_REFERENCES,
    COLOR_MASK_CONSENSUS_MIN_TOP_SCORE,
    CONSENSUS_MIN_DISTINCT_REFERENCES,
    CONSENSUS_MIN_TOP_INLIERS,
    HIGH_PURITY_CONSENSUS_MIN_DISTINCT_REFERENCES,
    HIGH_PURITY_CONSENSUS_MIN_TOP_INLIERS,
    PREFIX_COLOR_CONSENSUS_MIN_COMPANY_SCORE,
    PREFIX_COLOR_CONSENSUS_MIN_DISTINCT_REFERENCES,
    PREFIX_COLOR_CONSENSUS_MIN_PREFIX_CHARS,
    PREFIX_COLOR_CONSENSUS_MIN_TOP_SCORE,
)
from .seal_reference_gates import (
    _color_evidence_rank,
    _evidence_rank,
    _has_independent_short_company_fragment,
    _is_bare_company_requirement,
    _passes_chromatic_crop_consensus_vote,
    _passes_color_mask_consensus_vote,
    _passes_consensus_vote,
    _passes_high_purity_consensus_vote,
    _passes_prefix_color_mask_consensus_vote,
    _recognized_requirement_prefix_length,
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


def _best_color_mask_consensus(
    all_evidence: list[dict], seal_check: dict,
) -> dict:
    """Find two-file whole-ink consensus for a partially read bare company seal."""
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    if (
        seal_check.get("company_conflict") is True
        or not _is_bare_company_requirement(requirement)
        or float(seal_check.get("company_score", 0))
        < COLOR_MASK_CONSENSUS_MIN_COMPANY_SCORE
        or not _has_independent_short_company_fragment(
            seal_check, requirement
        )
    ):
        return {
            "accepted": False,
            "candidate_index": -1,
            "reference_count": 0,
            "top_score": 0.0,
            "matches": [],
        }

    by_candidate: dict[int, dict[str, dict]] = defaultdict(dict)
    for evidence in all_evidence:
        if not _passes_color_mask_consensus_vote(evidence):
            continue
        candidate_index = int(evidence.get("candidate_index", -1))
        reference_filename = str(evidence.get("reference_filename") or "")
        if candidate_index < 0 or not reference_filename:
            continue
        previous = by_candidate[candidate_index].get(reference_filename)
        if (
            previous is None
            or _color_evidence_rank(evidence) > _color_evidence_rank(previous)
        ):
            by_candidate[candidate_index][reference_filename] = evidence

    groups = []
    for candidate_index, distinct in by_candidate.items():
        matches = sorted(
            distinct.values(), key=_color_evidence_rank, reverse=True
        )
        top_score = float(matches[0].get("color_mask_score", 0)) if matches else 0
        groups.append({
            "candidate_index": candidate_index,
            "reference_count": len(matches),
            "top_score": top_score,
            "accepted": bool(
                len(matches) >= COLOR_MASK_CONSENSUS_MIN_DISTINCT_REFERENCES
                and top_score >= COLOR_MASK_CONSENSUS_MIN_TOP_SCORE
            ),
            "matches": [
                {
                    key: item.get(key, 0)
                    for key in (
                        "reference_filename", "reference_url",
                        "color_mask_score", "color_mask_correlation",
                        "color_mask_dice", "good_matches",
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
            "top_score": 0.0,
            "matches": [],
        }
    return max(
        groups,
        key=lambda item: (
            bool(item["accepted"]),
            int(item["reference_count"]),
            float(item["top_score"]),
            sum(float(row["color_mask_score"]) for row in item["matches"]),
        ),
    )


def _best_prefix_color_mask_consensus(
    all_evidence: list[dict], seal_check: dict,
) -> dict:
    """Find two-file whole-ink consensus backed by a long exact OCR prefix."""
    requirement = normalize_text(str(seal_check.get("requirement") or ""))
    prefix_length = _recognized_requirement_prefix_length(
        seal_check, requirement
    )
    if (
        seal_check.get("company_conflict") is True
        or not re.fullmatch(r"[\u4e00-\u9fff]{8,20}", requirement)
        or _is_bare_company_requirement(requirement)
        or float(seal_check.get("company_score", 0))
        < PREFIX_COLOR_CONSENSUS_MIN_COMPANY_SCORE
        or prefix_length < PREFIX_COLOR_CONSENSUS_MIN_PREFIX_CHARS
    ):
        return {
            "accepted": False,
            "candidate_index": -1,
            "reference_count": 0,
            "top_score": 0.0,
            "recognized_prefix_length": prefix_length,
            "matches": [],
        }

    by_candidate: dict[int, dict[str, dict]] = defaultdict(dict)
    for evidence in all_evidence:
        if not _passes_prefix_color_mask_consensus_vote(evidence):
            continue
        candidate_index = int(evidence.get("candidate_index", -1))
        reference_filename = str(evidence.get("reference_filename") or "")
        if candidate_index < 0 or not reference_filename:
            continue
        previous = by_candidate[candidate_index].get(reference_filename)
        if (
            previous is None
            or _color_evidence_rank(evidence) > _color_evidence_rank(previous)
        ):
            by_candidate[candidate_index][reference_filename] = evidence

    groups = []
    for candidate_index, distinct in by_candidate.items():
        matches = sorted(
            distinct.values(), key=_color_evidence_rank, reverse=True
        )
        top_score = float(matches[0].get("color_mask_score", 0)) if matches else 0
        groups.append({
            "candidate_index": candidate_index,
            "reference_count": len(matches),
            "top_score": top_score,
            "recognized_prefix_length": prefix_length,
            "accepted": bool(
                len(matches)
                >= PREFIX_COLOR_CONSENSUS_MIN_DISTINCT_REFERENCES
                and top_score >= PREFIX_COLOR_CONSENSUS_MIN_TOP_SCORE
            ),
            "matches": [
                {
                    key: item.get(key, 0)
                    for key in (
                        "reference_filename", "reference_url",
                        "color_mask_score", "color_mask_correlation",
                        "color_mask_dice", "good_matches",
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
            "top_score": 0.0,
            "recognized_prefix_length": prefix_length,
            "matches": [],
        }
    return max(
        groups,
        key=lambda item: (
            bool(item["accepted"]),
            int(item["reference_count"]),
            float(item["top_score"]),
            sum(float(row["color_mask_score"]) for row in item["matches"]),
        ),
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
